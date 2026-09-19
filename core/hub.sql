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

-- The fields a record must have. A system that carries only some of a record
-- -- an address table beside a customer table -- does not own its lifecycle:
-- its rows going away empty its fields, they do not delete the customer.
alter table hub.entity add column if not exists required text[] not null default '{}';

create table if not exists hub.system (
    entity text not null references hub.entity(name),
    system text not null,
    primary key (entity, system)
);
-- The fields a system receives, and so the only ones it can echo back. Null
-- is all of them. Awaiting a field from a system that never gets it would
-- wait for ever.
alter table hub.system add column if not exists fields text[];

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

-- Systems that do not share a key (#80). `keys` names, per system, the golden
-- column holding that system's own key for the record -- the crosswalk -- and
-- the golden record's key is then the hub's own, from `hub.record_id`. The
-- local keys are columns of the golden record, not a table beside it, because
-- the delivery back is a SeaTunnel job that cannot look anything up: the key
-- has to be in the row it reads.
alter table hub.entity add column if not exists keys jsonb;
create sequence if not exists hub.record_id;
-- How a row under a key the hub has not seen finds its record: golden columns
-- that must all be equal, a tax identifier. Declared by the flow (`linkBy`).
alter table hub.system add column if not exists link_by text[];
-- The local keys a deleted record had, so a late change under one of them
-- still meets its tombstone rather than looking like a new record.
alter table hub.tombstone add column if not exists aliases jsonb;

-- Rows the hub could not place, waiting for a person (`hub.link`). A
-- duplicate made quietly is the one outcome worse than waiting.
create table if not exists hub.unmatched (
    entity text  not null,
    system text  not null,
    local  jsonb not null,
    ms     bigint not null,
    row    jsonb not null,
    -- 'ambiguous': several records match; 'taken': the one that matches has
    -- another key of this system already; 'unmatchable': nothing to match by;
    -- 'part': part of a record whose owning row has not placed it yet.
    reason text  not null,
    at     timestamptz not null default now(),
    primary key (entity, system, local)
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

-- The golden key of the record a row belongs to, for a system with keys of
-- its own; null when the row is held instead. A key seen before answers at
-- once, a deleted record's too. An unseen one is matched by the system's
-- rule: one record without a key of this system is it, none is a new record,
-- anything else waits. A row carrying the golden key already is a person's
-- decision being replayed (`hub.link`), and is trusted.
create or replace function hub.resolve(p_entity text, p_system text, p_kind text,
                                       p_ms bigint, p_row jsonb,
                                       out hk jsonb, out linked boolean, out fresh boolean)
language plpgsql as $$
declare
    e     hub.entity;
    kc    text;
    ac    text;
    lk    jsonb;
    rule  text[];
    probe jsonb;
    hits  jsonb[];
    why   text;
begin
    linked := false;
    fresh := false;
    select * into e from hub.entity x where x.name = p_entity;
    kc := e.key[1];
    ac := e.keys ->> split_part(p_system, '.', 1);
    lk := jsonb_build_object(ac, p_row -> ac);
    -- Two rows under one new key, from two tables of a system, place it once.
    perform pg_advisory_xact_lock(hashtextextended(p_entity || split_part(p_system, '.', 1) || lk::text, 0));

    if p_row ? kc then
        execute format('select array_agg(to_jsonb(g)) from hub.%I g, jsonb_populate_record(null::hub.%I, $1) r '
                       'where g.%I = r.%I', p_entity, p_entity, kc, kc) into hits using p_row;
        hk := jsonb_build_object(kc, p_row -> kc);
        linked := hits is not null and hits[1] -> ac is distinct from p_row -> ac;
        fresh := true;
    else
        execute format('select array_agg(to_jsonb(g)) from hub.%I g, jsonb_populate_record(null::hub.%I, $1) r '
                       'where g.%I = r.%I', p_entity, p_entity, ac, ac) into hits using lk;
        if hits is not null then
            hk := jsonb_build_object(kc, hits[1] -> kc);
            return;
        end if;
        select t.key into hk from hub.tombstone t
         where t.entity = p_entity and t.aliases @> lk
         order by t.deleted_ms desc limit 1;
        if hk is not null then
            return;
        end if;
        if p_kind = 'DELETE' then
            -- Never placed, now gone: nothing left to decide.
            delete from hub.unmatched u
             where u.entity = p_entity and u.system = p_system and u.local = lk;
            return;
        end if;
        select s.link_by into rule from hub.system s
         where s.entity = p_entity and s.system = p_system;
        probe := (select jsonb_object_agg(c, p_row -> c) from unnest(rule) c);
        if exists (select 1 from unnest(e.required) r where not p_row ? r) then
            why := 'part';
        elsif probe is null or exists (select 1 from jsonb_each(probe) v where v.value = 'null') then
            why := 'unmatchable';
        else
            -- Two systems bringing one new customer at once make one record.
            perform pg_advisory_xact_lock(hashtextextended(p_entity || probe::text, 0));
            execute format('select array_agg(to_jsonb(g)) from hub.%I g, jsonb_populate_record(null::hub.%I, $1) r '
                           'where %s', p_entity, p_entity,
                           (select string_agg(format('g.%I = r.%I', c, c), ' and ') from unnest(rule) c))
                into hits using probe;
            if cardinality(hits) > 1 then
                why := 'ambiguous';
            elsif hits is not null and hits[1] -> ac <> 'null'::jsonb then
                why := 'taken';
            elsif hits is not null then
                hk := jsonb_build_object(kc, hits[1] -> kc);
                linked := true;
            else
                hk := jsonb_build_object(kc, nextval('hub.record_id'));
            end if;
        end if;
        if hk is null then
            insert into hub.unmatched (entity, system, local, ms, row, reason)
            values (p_entity, p_system, lk, p_ms, p_row, why)
            on conflict (entity, system, local) do update
            set ms = excluded.ms, row = excluded.row, reason = excluded.reason, at = now();
            return;
        end if;
        fresh := true;
    end if;
    delete from hub.unmatched u
     where u.entity = p_entity and u.system = p_system and u.local = lk;
end $$;

-- Rows held under a local key that now has a record, the owning row first.
-- `p_key` places them in a record a person chose.
create or replace function hub.replay(p_entity text, p_system text, p_local jsonb,
                                      p_key jsonb default null)
returns void language plpgsql as $$
declare
    u hub.unmatched;
begin
    for u in select x.* from hub.unmatched x, hub.entity e
              where x.entity = p_entity and e.name = p_entity and x.local = p_local
                and split_part(x.system, '.', 1) = split_part(p_system, '.', 1)
              order by (select count(*) from unnest(e.required) r where not x.row ? r), x.system
    loop
        delete from hub.unmatched x
         where x.entity = u.entity and x.system = u.system and x.local = u.local;
        perform hub.merge(u.entity, u.system, 'INSERT', u.ms, u.row || coalesce(p_key, '{}'));
    end loop;
end $$;

-- A person's decision on held rows: this system's key is that record, or,
-- with no record given, a record of its own.
create or replace function hub.link(p_entity text, p_system text, p_local jsonb,
                                    p_record jsonb default null)
returns jsonb language plpgsql as $$
declare
    e  hub.entity;
    ac text;
    g  jsonb;
begin
    select * into e from hub.entity x where x.name = p_entity;
    ac := e.keys ->> split_part(p_system, '.', 1);
    if ac is null then
        raise exception 'hub: % has no key of its own in %', p_system, p_entity;
    end if;
    if p_record is null then
        p_record := jsonb_build_object(e.key[1], nextval('hub.record_id'));
    else
        execute format('select to_jsonb(g) from hub.%I g, jsonb_populate_record(null::hub.%I, $1) r '
                       'where g.%I = r.%I', p_entity, p_entity, e.key[1], e.key[1])
            into g using p_record;
        if g is null then
            raise exception 'hub: % has no record %', p_entity, p_record;
        elsif g -> ac <> 'null'::jsonb and g -> ac <> p_local -> ac then
            raise exception 'hub: record % is already % in %', p_record, g -> ac, p_system;
        end if;
    end if;
    perform hub.replay(p_entity, p_system, p_local, p_record);
    return p_record;
end $$;

create or replace function hub.merge(p_entity text, p_system text, p_kind text,
                                     p_ms bigint, p_row jsonb)
returns void language plpgsql as $$
declare
    keycols text[];
    req     text[];
    kind    text := p_kind;
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
    -- Fields S won while a delivery of another value to S is still on its
    -- way: that delivery lands after, so S must get its own value again.
    back    text[] := '{}';
    meta    text[] := array['_at', '_by', '_rev', '_changed', '_skip'];
    clist   text;
    -- A system with keys of its own (#80): the golden column holding them,
    -- every such column (none is a field), and how this row was placed.
    xkeys   jsonb;
    ac      text;
    skip    text[];
    linked  boolean := false;
    fresh   boolean := false;
begin
    select e.key, e.authority, e.required, e.keys into keycols, auth, req, xkeys
      from hub.entity e where e.name = p_entity;
    if keycols is null then
        raise exception 'hub: % is not a registered entity', p_entity;
    end if;
    skip := keycols || array(select v from jsonb_each_text(coalesce(xkeys, '{}')) t(n, v));
    ac := xkeys ->> split_part(p_system, '.', 1);
    if xkeys is not null and ac is null then
        raise exception 'hub: % has no key column in %', p_system, p_entity;
    end if;
    -- Until it is placed, a row is known by its own system's key.
    select jsonb_object_agg(c, p_row -> c) into k
      from unnest(case when ac is null then keycols else array[ac] end) c;
    -- Two systems' changes to one record are merged one at a time.
    perform pg_advisory_xact_lock(hashtextextended(p_entity || k::text, 0));

    if kind = 'UPDATE_BEFORE' then
        insert into hub.pending_before values (p_system, p_entity, k, p_row)
            on conflict (system, entity, key) do update set row = excluded.row;
        return;
    elsif kind = 'UPDATE_AFTER' then
        delete from hub.pending_before p
         where p.system = p_system and p.entity = p_entity and p.key = k
        returning p.row into before;
        after := p_row;
    elsif kind = 'INSERT' then
        after := p_row;
    elsif kind = 'DELETE' and exists (select 1 from unnest(req) r where not p_row ? r) then
        -- A flow carrying only part of the record: its row going away empties
        -- its fields. The record itself belongs to a flow that carries all of
        -- what the record requires.
        before := p_row;
        after := (select jsonb_object_agg(e.key, case when e.key = any(skip)
                                                      then e.value else 'null'::jsonb end)
                    from jsonb_each(p_row) e);
        kind := 'UPDATE_AFTER';
    elsif kind = 'DELETE' then
        before := p_row;
    else
        raise exception 'hub: % is not a row kind', p_kind;
    end if;

    if ac is not null then
        select r.hk, r.linked, r.fresh into k, linked, fresh
          from hub.resolve(p_entity, p_system, p_kind, p_ms, coalesce(after, before)) r;
        if k is null then
            return;
        end if;
        perform pg_advisory_xact_lock(hashtextextended(p_entity || k::text, 0));
        after := after || k;
        before := before || k;
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
            -- Name the deleter first, so the delete's before image carries it
            -- and the delivery skips the system that already did it.
            execute format('update hub.%I g set _skip = $2, _changed = '''' '
                           'from jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k, p_system;
            execute format('delete from hub.%I g using jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k;
            insert into hub.tombstone (entity, key, deleted_ms, deleted_by, aliases)
            values (p_entity, k, p_ms, p_system,
                    (select jsonb_object_agg(v, g -> v) from jsonb_each_text(xkeys) t(s, v)
                      where g -> v <> 'null'::jsonb))
                on conflict (entity, key) do update
                set deleted_ms = excluded.deleted_ms, deleted_by = excluded.deleted_by,
                    aliases = excluded.aliases;
            perform hub.expect_add(s.system, p_entity, k, '*', null) from hub.system s
             where s.entity = p_entity and s.system <> p_system and s.fields is null;
        else
            insert into hub.conflict (entity, key, field, kept, kept_ms, lost, lost_by, lost_ms)
            values (p_entity, k, '*', g - meta, gt, null, p_system, p_ms);
            execute format('update hub.%I g set _rev = g._rev + 1, _changed = ''*'', _skip = null '
                           'from jsonb_populate_record(null::hub.%I, $1) r where %s',
                           p_entity, p_entity, keyed) using k;
            -- The system that deleted gets the row back, field by field --
            -- the fields it receives.
            perform hub.expect_add(p_system, p_entity, k, e.key, e.value)
               from jsonb_each(g - meta) e
               left join hub.system s on s.entity = p_entity and s.system = p_system
              where not e.key = any(skip)
                and (s.fields is null or e.key = any(s.fields));
        end if;
        return;
    end if;

    -- A record the hub does not have. First, is it our own write coming back?
    -- A delivery can arrive after the record was deleted.
    if g is null then
        -- Part of a record emptying for a record the hub no longer has: there
        -- is nothing left to empty.
        if p_kind = 'DELETE' then
            return;
        end if;
        for f, a in select e.key, e.value from jsonb_each(after) e loop
            continue when f = any(skip);
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
            if kind = 'UPDATE_AFTER' then
                insert into hub.conflict (entity, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms)
                values (p_entity, k, '*', after, p_system, p_ms, null, tby, tdel);
            end if;
            delete from hub.tombstone t where t.entity = p_entity and t.key = k;
        end if;
        -- New everywhere else.
        for f in select jsonb_object_keys(after) loop
            if not f = any(skip) then
                f_at := f_at || jsonb_build_object(f, p_ms);
                f_by := f_by || jsonb_build_object(f, p_system);
            end if;
        end loop;
        execute format('insert into hub.%I select * from jsonb_populate_record(null::hub.%I, $1)',
                       p_entity, p_entity)
            using after || jsonb_build_object('_at', f_at, '_by', f_by, '_rev', 0,
                                              '_changed', '*', '_skip', p_system);
        perform hub.expect_add(s.system, p_entity, k, e.key, e.value)
           from hub.system s, jsonb_each(after) e
          where s.entity = p_entity and s.system <> p_system and not e.key = any(skip)
            and (s.fields is null or e.key = any(s.fields));
        if fresh then
            perform hub.replay(p_entity, p_system, jsonb_build_object(ac, after -> ac));
        end if;
        return;
    end if;

    for f, a in select e.key, e.value from jsonb_each(after) e loop
        continue when f = any(skip);
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
        -- A field never set is filled: a table carrying part of a record
        -- arrives as an INSERT, and a gap is not a dispute. A field that was
        -- *emptied* has a commit time and is not a gap -- an older row
        -- reappearing must not refill it (the loop the live demo ran).
        if kind = 'INSERT' and g -> '_at' -> f is null then
            changed := changed || jsonb_build_object(f, a);
            f_at := f_at || jsonb_build_object(f, p_ms);
            f_by := f_by || jsonb_build_object(f, p_system);
            continue;
        end if;
        -- An empty value empties nothing when it arrives as a new row.
        continue when kind = 'INSERT' and a = 'null'::jsonb;
        -- A record the hub already has, arriving as new with a different
        -- value: a first sync, or two systems creating one key. Commit times
        -- say nothing about which value is right, so the authority's system
        -- stands -- any table of it.
        if kind = 'INSERT' and gv <> 'null'::jsonb then
            if split_part(p_system, '.', 1) = split_part(auth, '.', 1) then
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
            if not current and gv <> 'null'::jsonb then
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
    -- A field S won -- by any rule, a seed included -- while another value is
    -- still on its way to S: that delivery lands after, so S gets its own back.
    back := array(select c from jsonb_object_keys(changed) c
                   where exists (select 1 from hub.expect x
                                  where x.system = p_system and x.entity = p_entity
                                    and x.key = k and x.field = c));

    -- A record this system's key was just linked to: the key is news to that
    -- system's other tables, which could not be written without it.
    if linked then
        changed := changed || jsonb_build_object(ac, after -> ac);
    end if;

    -- What a delivery writes: only the fields this revision changed, so it
    -- never carries an unchanged field's value over an edit made in the
    -- target meanwhile. It skips the system the change came from, which
    -- already has it -- unless that system lost a field, or has another value
    -- still on its way to it.
    if changed <> '{}' then
        clist := ',' || (select string_agg(c, ',' order by c) from
                         (select jsonb_object_keys(changed) c union select unnest(lost)) x) || ',';
        -- A revision that fills a required field the record was missing makes
        -- it whole: to a target that never had it, that is a new record.
        if exists (select 1 from jsonb_object_keys(changed) c
                    where c = any(req) and g -> c = 'null'::jsonb) then
            clist := '*';
        end if;
        execute format('update hub.%I g set %s, _at = g._at || $2, _by = g._by || $3, '
                       '_rev = g._rev + 1, _changed = $4, _skip = $5 '
                       'from jsonb_populate_record(null::hub.%I, $1) r where %s',
                       p_entity,
                       (select string_agg(format('%I = r.%I', c, c), ', ') from jsonb_object_keys(changed) c),
                       p_entity, keyed)
            using k || changed, f_at, f_by, clist,
                  case when cardinality(lost) > 0 or cardinality(back) > 0 then null else p_system end;
        perform hub.expect_add(s.system, p_entity, k, e.key, e.value)
           from hub.system s, jsonb_each(changed) e
          where s.entity = p_entity and s.system <> p_system and not e.key = any(skip)
            and (s.fields is null or e.key = any(s.fields));
        perform hub.expect_add(p_system, p_entity, k, bf.name, changed -> bf.name)
           from unnest(back) bf(name);
    elsif cardinality(lost) > 0 then
        -- The losing system is corrected by the next delivery; a revision bump
        -- makes one happen even when nothing else changed.
        execute format('update hub.%I g set _rev = g._rev + 1, _changed = $2, _skip = null '
                       'from jsonb_populate_record(null::hub.%I, $1) r where %s',
                       p_entity, p_entity, keyed)
            using k, ',' || array_to_string(lost, ',') || ',';
    end if;

    if cardinality(lost) > 0 then
        perform hub.expect_add(p_system, p_entity, k, l.name, g -> l.name)
           from unnest(lost) l(name);
    end if;
    -- Placed just now: what was held under the same key follows it.
    if fresh then
        perform hub.replay(p_entity, p_system, jsonb_build_object(ac, after -> ac));
    end if;
end $$;

-- `fields` is the flow's own list, comma-separated. A flow carrying part of a
-- record leaves the inbox's other columns null, and those nulls are not
-- values: only the fields the flow declares are merged.
create or replace function hub.on_inbox() returns trigger language plpgsql as $$
declare
    r jsonb := to_jsonb(new) - 'id' - 'system' - 'row_kind' - 'source_ms'
                             - 'landed_at' - 'fields';
begin
    if new.fields is not null then
        r := (select jsonb_object_agg(f, r -> f)
                from unnest(string_to_array(new.fields, ',')) f);
    end if;
    perform hub.merge(tg_argv[0], new.system, new.row_kind, new.source_ms, r);
    return null;
end $$;
