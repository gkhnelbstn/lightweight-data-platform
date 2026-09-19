-- Many rows into one row, one way (#81): invoice lines in one system become
-- one journal entry per invoice in another. core/flow_aggregate.py.
--
-- SeaTunnel's SQL has no aggregation and no joins (ADR 0020), so the lines
-- land here -- a mirror of the source table, kept by a trigger on an
-- append-only inbox -- and every group a line change touches is recomputed
-- into one row of `flow.<id>`. A second job carries that table to the target.
-- Recomputing the group, rather than adding and subtracting, is what keeps
-- an update, a delete and a line moved between invoices all correct.

create schema if not exists flow;

-- Each aggregating flow's shape: the source columns naming one line, the
-- group (target column: source column) and the totals (target column:
-- [function, source column or '*']).
create table if not exists flow.spec (
    flow     text primary key,
    line_key text[] not null,
    grp      jsonb  not null,
    aggs     jsonb  not null
);

-- An update arrives as a before and an after row; the before waits here. One
-- per flow: a job has one reader, and the two rows are consecutive.
create table if not exists flow.pending (
    flow text  primary key,
    row  jsonb not null
);

-- One group of a flow, recomputed from its lines: written only when it
-- changed, so a recomputation that changes nothing sends nothing on; deleted
-- when its last line has gone.
create or replace function flow.recompute(p_flow text, p_line jsonb)
returns void language plpgsql as $$
declare
    s    flow.spec;
    tgt  text[];
    src  text[];
    cond text;
    aggc text[];
    aggx text[];
    has  boolean;
begin
    select * into s from flow.spec where flow = p_flow;
    select array_agg(key order by key), array_agg(value order by key) into tgt, src
      from jsonb_each_text(s.grp);
    select array_agg(key order by key),
           array_agg(case when value ->> 1 = '*' then format('%s(*)', value ->> 0)
                          else format('%s(l.%I)', value ->> 0, value ->> 1) end order by key)
      into aggc, aggx from jsonb_each(s.aggs);
    cond := (select string_agg(format('l.%I is not distinct from r.%I', c, c), ' and ')
               from unnest(src) c);
    execute format('select exists (select 1 from flow.%I l, jsonb_populate_record(null::flow.%I, $1) r '
                   'where %s)', p_flow || '_lines', p_flow || '_lines', cond)
        into has using p_line;
    if not has then
        -- The group's row, named by the target's columns.
        execute format('delete from flow.%I a using jsonb_populate_record(null::flow.%I, $1) g '
                       'where %s', p_flow, p_flow,
                       (select string_agg(format('a.%I is not distinct from g.%I', t, t), ' and ')
                          from unnest(tgt) t))
            using (select jsonb_object_agg(t, p_line -> src[i])
                     from unnest(tgt) with ordinality u(t, i));
        return;
    end if;
    execute format(
        'insert into flow.%I as a (%s) select %s from flow.%I l, jsonb_populate_record(null::flow.%I, $1) r '
        'where %s group by %s on conflict (%s) do update set %s where (%s) is distinct from (%s)',
        p_flow,
        (select string_agg(format('%I', c), ', ') from unnest(tgt || aggc) c),
        (select string_agg(format('l.%I', src[i]), ', ') from unnest(tgt) with ordinality u(t, i))
            || ', ' || array_to_string(aggx, ', '),
        p_flow || '_lines', p_flow || '_lines', cond,
        (select string_agg(format('l.%I', c), ', ') from unnest(src) c),
        (select string_agg(format('%I', c), ', ') from unnest(tgt) c),
        (select string_agg(format('%I = excluded.%I', c, c), ', ') from unnest(aggc) c),
        (select string_agg(format('a.%I', c), ', ') from unnest(aggc) c),
        (select string_agg(format('excluded.%I', c), ', ') from unnest(aggc) c))
        using p_line;
end $$;

-- A line change, landed by SeaTunnel: kept in the mirror, and every group it
-- leaves or joins recomputed.
create or replace function flow.apply() returns trigger language plpgsql as $$
declare
    f      text := tg_argv[0];
    s      flow.spec;
    r      jsonb := to_jsonb(new) - 'id' - 'row_kind' - 'landed_at';
    before jsonb;
    was    jsonb;
    keyed  text;
    cols   text;
begin
    select * into s from flow.spec where flow = f;
    if new.row_kind = 'UPDATE_BEFORE' then
        insert into flow.pending values (f, r)
            on conflict (flow) do update set row = excluded.row;
        return null;
    end if;
    keyed := (select string_agg(format('l.%I is not distinct from x.%I', c, c), ' and ')
                from unnest(s.line_key) c);
    if new.row_kind = 'UPDATE_AFTER' then
        delete from flow.pending p where p.flow = f returning p.row into before;
        -- The line's key itself changed: the old line goes, from its group.
        if before is not null and exists (select 1 from unnest(s.line_key) c
                                           where before -> c is distinct from r -> c) then
            execute format('delete from flow.%I l using jsonb_populate_record(null::flow.%I, $1) x '
                           'where %s returning to_jsonb(l)', f || '_lines', f || '_lines', keyed)
                into was using before;
            if was is not null then
                perform flow.recompute(f, was);
            end if;
        end if;
    end if;
    -- The line as the mirror has it: its group may be the one it is leaving.
    execute format('select to_jsonb(l) from flow.%I l, jsonb_populate_record(null::flow.%I, $1) x '
                   'where %s', f || '_lines', f || '_lines', keyed) into was using r;
    if new.row_kind = 'DELETE' then
        execute format('delete from flow.%I l using jsonb_populate_record(null::flow.%I, $1) x '
                       'where %s', f || '_lines', f || '_lines', keyed) using r;
        perform flow.recompute(f, coalesce(was, r));
        return null;
    end if;
    cols := (select string_agg(format('%I = excluded.%I', c, c), ', ')
               from jsonb_object_keys(r) c where not c = any(s.line_key));
    execute format('insert into flow.%I select * from jsonb_populate_record(null::flow.%I, $1) '
                   'on conflict (%s) do update set %s', f || '_lines', f || '_lines',
                   (select string_agg(format('%I', c), ', ') from unnest(s.line_key) c), cols)
        using r;
    if was is not null then
        perform flow.recompute(f, was);
    end if;
    perform flow.recompute(f, r);
    return null;
end $$;
