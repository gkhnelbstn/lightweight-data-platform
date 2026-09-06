-- The two databases the contracts need: the data under test, and the results
-- about it. Postgres runs this once, when the volume is empty.
create database erp;
create database dq;
-- The target of the sync rules. Logical replication does not create tables on
-- the subscriber and neither does anything else, so core/sync.py builds them
-- from the contract; it still needs a database to build them in.
create database erp_replica;
-- The warehouse the medallion demo builds into. A separate database rather
-- than a schema, because that is what it is in practice -- and because
-- Postgres cannot query across databases, which is precisely why the loader
-- runs outside them and why ODD has to be *told* the lineage.
create database dwh;

-- Contract checks are SQL a person wrote, executed against the source. They
-- should not be able to write to it, and they should not be able to sit on a
-- lock all night. A role with SELECT and a statement timeout is the cheapest
-- version of both; it is not a sandbox, and the README says so.
create role dq_reader with login password 'dq_reader';
alter role dq_reader set statement_timeout = '60s';
alter role dq_reader set idle_in_transaction_session_timeout = '60s';
alter role dq_reader set default_transaction_read_only = on;

-- The warehouse the medallion demo builds into. Same read-only role: the
-- checks run against it too, and a contract on a mart is still a contract.
\connect dwh
grant connect on database dwh to dq_reader;
do $$ declare s text; begin
  foreach s in array array['raw','stg','fct','dim','mart',
                           'asof_stg','asof_fct','asof_mart'] loop
    execute format('create schema if not exists %I', s);
    execute format('grant usage on schema %I to dq_reader', s);
    execute format('grant select on all tables in schema %I to dq_reader', s);
    execute format('alter default privileges in schema %I '
                   'grant select on tables to dq_reader', s);
  end loop; end $$;

\connect erp
grant connect on database erp to dq_reader;
grant usage on schema public to dq_reader;
grant select on all tables in schema public to dq_reader;
-- and on whatever the seed and the daily views create later
alter default privileges in schema public grant select on tables to dq_reader;
