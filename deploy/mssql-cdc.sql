-- Turn on SQL Server's own change data capture for the demo ERP.
--
-- CDC is not something this project implements: SQL Server writes every
-- insert, update and delete into a change table for you, from the log, and
-- exposes it as an ordinary function you can SELECT from. That is the whole
-- reason core/sync.py is small -- it reads a table.
--
-- Two things the docs bury:
--   * It is a SQL Server Agent feature. `sp_cdc_enable_table` returns success
--     with the Agent stopped and then nothing ever lands in the change table.
--     compose.yaml sets MSSQL_AGENT_ENABLED for exactly this.
--   * Developer and Enterprise have it; Express does not. The image defaults
--     to Developer, which is why the demo can show it at all.
--
--   docker compose exec -T mssql /opt/mssql-tools18/bin/sqlcmd \
--     -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d erp -i /tmp/mssql-cdc.sql
use erp;
go

if (select is_cdc_enabled from sys.databases where name = 'erp') = 0
    exec sys.sp_cdc_enable_db;
go

declare @t sysname;
declare tables cursor for
    select name from sys.tables
    where schema_id = schema_id('dbo')
      and name in ('customers', 'products', 'sales_orders', 'sales_order_lines');
open tables;
fetch next from tables into @t;
while @@fetch_status = 0
begin
    if not exists (select 1 from cdc.change_tables ct
                   where ct.source_object_id = object_id('dbo.' + @t))
        -- @role_name null: reading the change table needs no extra grant
        -- beyond SELECT on the source, which is what the sync role has.
        exec sys.sp_cdc_enable_table
            @source_schema = 'dbo', @source_name = @t,
            @role_name = null, @supports_net_changes = 1;
    fetch next from tables into @t;
end
close tables; deallocate tables;
go

select ct.capture_instance,
       object_name(ct.source_object_id) as source_table,
       ct.supports_net_changes
from cdc.change_tables ct order by 2;
go
