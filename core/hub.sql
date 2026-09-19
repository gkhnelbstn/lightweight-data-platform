-- The hub: where both sides of a two-way integration meet. ADR 0021.
--
-- SeaTunnel carries every change from a system into `hub.<entity>_inbox`,
-- append-only, with its row kind and its *commit* time. A trigger merges each
-- row into the golden record `hub.<entity>`, field by field, and SeaTunnel
-- carries the golden record back out to every system. Everything here is the
-- per-row state no adoptable tool keeps for SQL Server: what was agreed, when
-- each field last changed and where, and which writes of ours are still on
-- their way back.

create schema if not exists hub;

create table if not exists hub.entity (
    name      text primary key,
    key       text[] not null,
    -- Whose values stand when two systems already hold the same record: the
    -- first sync, or two systems creating one key. After that, the later
    -- commit wins.
    authority text not null
);

create table if not exists hub.system (
    entity text not null references hub.entity(name),
    system text not null,
    primary key (entity, system)
);

-- An update arrives as two rows, the before image first. It waits here for
-- its after image.
create table if not exists hub.pending_before (
    system text  not null,
    entity text  not null,
    key    jsonb not null,
    row    jsonb not null,
    primary key (system, entity, key)
);

-- Values the hub has sent to a system and will see again when that system's
-- CDC reports the write. Recognising them is what stops an echo; their order
-- matters, because a delivery can come back after the golden record has
-- moved on. Field '*' with a null value is a delete.
create table if not exists hub.expect (
    seq        bigserial primary key,
    system     text  not null,
    entity     text  not null,
    key        jsonb not null,
    field      text  not null,
    value      jsonb,
    created_at timestamptz not null default now()
);
create index if not exists expect_lookup on hub.expect (system, entity, key, field, seq);

-- Every time two systems changed the same field without seeing each other's
-- change. Nothing is lost silently: the losing value is here.
create table if not exists hub.conflict (
    id      bigserial primary key,
    at      timestamptz not null default now(),
    entity  text  not null,
    key     jsonb not null,
    field   text  not null,
    kept    jsonb, kept_by text, kept_ms bigint,
    lost    jsonb, lost_by text, lost_ms bigint,
    -- 'edit': both changed it without seeing each other; the later commit won.
    -- 'seed': both already held it; the authority's value won.
    reason  text  not null default 'edit'
);

-- A record deleted in the hub, and when. Without it, a change arriving for a
-- record the hub no longer has looks like a new record -- which is how a late
-- delivery once resurrected a deleted customer.
create table if not exists hub.tombstone (
    entity     text   not null,
    key        jsonb  not null,
    deleted_ms bigint not null,
    deleted_by text   not null,
    primary key (entity, key)
);

-- True, and forgotten, when this value is one the hub sent and is waiting to
-- see again. Everything older for the same field goes too: it was
-- superseded. An expectation older than an hour is dropped, because a
-- delivery that never produced a change -- the value was already there --
-- would otherwise wait for ever.
create or replace function hub.consume(p_system text, p_entity text, p_key jsonb,
                                       p_field text, p_value jsonb)
returns boolean language plpgsql as $$
declare
    hit bigint;
begin
    delete from hub.expect x
     where x.system = p_system and x.entity = p_entity and x.key = p_key
       and x.field = p_field and x.created_at < now() - interval '1 hour';
    select min(seq) into hit from hub.expect x
     where x.system = p_system and x.entity = p_entity and x.key = p_key
       and x.field = p_field and x.value is not distinct from p_value;
    if hit is null then
        return false;
    end if;
    delete from hub.expect x
     where x.system = p_system and x.entity = p_entity and x.key = p_key
       and x.field = p_field and x.seq <= hit;
    return true;
end $$;

-- Wait for this value to come back from this system, unless the newest thing
-- already awaited there is the same value: one write comes back once.
create or replace function hub.expect_add(p_system text, p_entity text, p_key jsonb,
                                          p_field text, p_value jsonb)
returns void language plpgsql as $$
begin
    if exists (select 1 from hub.expect x
                where x.system = p_system and x.entity = p_entity and x.key = p_key
                  and x.field = p_field and x.value is not distinct from p_value
                  and x.seq = (select max(y.seq) from hub.expect y
                                where y.system = p_system and y.entity = p_entity
                                  and y.key = p_key and y.field = p_field)) then
        return;
    end if;
    insert into hub.expect (system, entity, key, field, value)
    values (p_system, p_entity, p_key, p_field, p_value);
end $$;

create or replace function hub.merge(p_entity text, p_system text, p_kind text,
                                     p_ms bigint, p_row jsonb)
returns void language plpgsql as $$
declare
    keycols text[];
    auth    text;
    k       jsonb;
    keyed   text;
    before  jsonb;
    after   jsonb;
    g       jsonb;
    f       text;
    a       jsonb;
    b       jsonb;
    gv      jsonb;
    gt      bigint;
    gby     text;
    current boolean;
    genuine boolean := false;
    tdel    bigint;
    tby     text;
    changed jsonb := '{}';
    f_at    jsonb := '{}';
    f_by    jsonb := '{}';
    lost    text[] := '{}';
begin
    select e.key, e.authority into keycols, auth from hub.entity e where e.name = p_entity;
    if keycols is null then
        raise exception 'hub: % is not a registered entity', p_entity;
    end if;
    select jsonb_object_agg(c, p_row -> c) into k from unnest(keycols) c;
    -- Two systems' changes to one record are merged one at a time.
    perform pg_advisory_xact_lock(hashtextextended(p_entity || k::text, 0));

    if p_kind = 'UPDATE_BEFORE' then
        insert into hub.pending_before values (p_system, p_entity, k, p_row)
            on conflict (system, entity, key) do update set row = excluded.row;
        return;
    elsif p_kind = 'UPDATE_AFTER' then
        delete from hub.pending_before p
         where p.system = p_system and p.entity = p_entity and p.key = k
        returning p.row into before;
        after := p_row;
    elsif p_kind = 'INSERT' then
        after := p_row;
    elsif p_kind = 'DELETE' then
        before := p_row;
    else
        raise exception 'hub: % is not a row kind', p_kind;
    end if;

    keyed := (select string_agg(format('g.%I is not distinct from r.%I', c, c), ' and ')
                from unnest(keycols) c);
    execute format('select to_jsonb(g) from hub.%I g, jsonb_populate_record(null::hub.%I, $1) r where %s',
                   p_entity, p_entity, keyed) into g using k;

    -- A delete: the latest commit decides, against the newest field.
    if after is null then
        if hub.consume(p_system, p_entity, k, '*', null) or g is null then
            return;
        end if;
        gt := (select coalesce(max(v::bigint), 0) from jsonb_each_text(g -> '_at') t(n, v));
        if p_ms >= gt then
            execute format('delete from hub.%I g using jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k;
            insert into hub.tombstone values (p_entity, k, p_ms, p_system)
                on conflict (entity, key) do update
                set deleted_ms = excluded.deleted_ms, deleted_by = excluded.deleted_by;
            perform hub.expect_add(s.system, p_entity, k, '*', null) from hub.system s
             where s.entity = p_entity and s.system <> p_system;
        else
            insert into hub.conflict (entity, key, field, kept, kept_ms, lost, lost_by, lost_ms)
            values (p_entity, k, '*', g - '_at' - '_by' - '_rev', gt, null, p_system, p_ms);
            execute format('update hub.%I g set _rev = g._rev + 1 from jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k;
            -- The system that deleted gets the row back, field by field.
            perform hub.expect_add(p_system, p_entity, k, e.key, e.value)
               from jsonb_each(g - '_at' - '_by' - '_rev') e
              where not e.key = any(keycols);
        end if;
        return;
    end if;

    -- A record the hub does not have. First, is it our own write coming back?
    -- A delivery can arrive after the record was deleted.
    if g is null then
        for f, a in select e.key, e.value from jsonb_each(after) e loop
            continue when f = any(keycols);
            continue when hub.consume(p_system, p_entity, k, f, a);
            continue when before is not null and (before -> f) is not distinct from a;
            genuine := true;
        end loop;
        if not genuine then
            return;
        end if;
        -- Then, was it deleted? The later commit wins against the delete too.
        select t.deleted_ms, t.deleted_by into tdel, tby
          from hub.tombstone t where t.entity = p_entity and t.key = k;
        if tdel is not null and p_ms <= tdel then
            insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms)
            values (p_entity, k, '*', null, tby, tdel, after, p_system, p_ms);
            return;
        end if;
        if tdel is not null then
            if p_kind = 'UPDATE_AFTER' then
                insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms)
                values (p_entity, k, '*', after, p_system, p_ms, null, tby, tdel);
            end if;
            delete from hub.tombstone t where t.entity = p_entity and t.key = k;
        end if;
        -- New everywhere else.
        for f in select jsonb_object_keys(after) loop
            if not f = any(keycols) then
                f_at := f_at || jsonb_build_object(f, p_ms);
                f_by := f_by || jsonb_build_object(f, p_system);
            end if;
        end loop;
        execute format('insert into hub.%I select * from jsonb_populate_record(null::hub.%I, $1)',
                       p_entity, p_entity)
            using after || jsonb_build_object('_at', f_at, '_by', f_by, '_rev', 0);
        perform hub.expect_add(s.system, p_entity, k, e.key, e.value)
           from hub.system s, jsonb_each(after) e
          where s.entity = p_entity and s.system <> p_system and not e.key = any(keycols);
        return;
    end if;

    for f, a in select e.key, e.value from jsonb_each(after) e loop
        continue when f = any(keycols);
        -- Our own write, on its way back.
        continue when hub.consume(p_system, p_entity, k, f, a);
        b := before -> f;
        -- A field this change did not touch.
        continue when before is not null and b is not distinct from a;
        gv := g -> f;
        -- Already agreed.
        continue when a is not distinct from gv;
        gt := coalesce((g -> '_at' ->> f)::bigint, 0);
        gby := g -> '_by' ->> f;
        -- A record the hub already has, arriving as new: a first sync, or two
        -- systems creating one key. Neither is an edit, and commit times say
        -- nothing about which value is right, so the authority's stands.
        if p_kind = 'INSERT' then
            if p_system = auth then
                insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms, reason)
                values (p_entity, k, f, a, p_system, p_ms, gv, gby, gt, 'seed');
                changed := changed || jsonb_build_object(f, a);
                f_at := f_at || jsonb_build_object(f, p_ms);
                f_by := f_by || jsonb_build_object(f, p_system);
            else
                insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms, reason)
                values (p_entity, k, f, gv, gby, gt, a, p_system, p_ms, 'seed');
                lost := lost || f;
            end if;
            continue;
        end if;
        -- The system edited the value everyone has: no conflict, whatever the
        -- clocks say. Otherwise it edited something stale, and the later
        -- commit wins; a tie goes to the system name, so every replay agrees.
        current := before is not null and b is not distinct from gv;
        if current or p_ms > gt or (p_ms = gt and p_system > coalesce(gby, '')) then
            if not current then
                insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms)
                values (p_entity, k, f, a, p_system, p_ms, gv, gby, gt);
            end if;
            changed := changed || jsonb_build_object(f, a);
            f_at := f_at || jsonb_build_object(f, p_ms);
            f_by := f_by || jsonb_build_object(f, p_system);
        else
            insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms)
            values (p_entity, k, f, gv, gby, gt, a, p_system, p_ms);
            lost := lost || f;
        end if;
    end loop;

    if changed <> '{}' then
        execute format('update hub.%I g set %s, _at = g._at || $2, _by = g._by || $3, _rev = g._rev + 1 '
                       'from jsonb_populate_record(null::hub.%I, $1) r where %s',
                       p_entity,
                       (select string_agg(format('%I = r.%I', c, c), ', ') from jsonb_object_keys(changed) c),
                       p_entity, keyed)
            using k || changed, f_at, f_by;
        perform hub.expect_add(s.system, p_entity, k, e.key, e.value)
           from hub.system s, jsonb_each(changed) e
          where s.entity = p_entity and s.system <> p_system;
    end if;

    if cardinality(lost) > 0 then
        -- The losing system is corrected by the next delivery; a revision bump
        -- makes one happen even when nothing else changed.
        if changed = '{}' then
            execute format('update hub.%I g set _rev = g._rev + 1 from jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k;
        end if;
        perform hub.expect_add(p_system, p_entity, k, l.name, g -> l.name)
           from unnest(lost) l(name);
    end if;
end $$;

create or replace function hub.on_inbox() returns trigger language plpgsql as $$
begin
    perform hub.merge(tg_argv[0], new.system, new.row_kind, new.source_ms,
                      to_jsonb(new) - 'id' - 'system' - 'row_kind' - 'source_ms' - 'landed_at');
    return null;
end $$;
