/* An ERP-shaped SQL Server database with enough rows to be worth cataloguing,
   and enough wrong ones to be worth checking.

   Set-based generation on purpose: a row-at-a-time loop takes minutes for the
   same result, and this has to be re-runnable while iterating on the demo.

   The defects are deliberate and each one is a different failure a contract
   should catch:
     - customers.tax_id      null on ~2% (completeness)
     - customers.email       ~1% missing an @ (conformity)
     - orders.customer_id    ~0.5% orphaned (referential integrity)
     - orders.currency       a handful outside TRY/USD/EUR (accepted values)
     - order_lines           ~1% where the header total disagrees (consistency)
     - one day with a third of the usual volume (volume anomaly)
*/
if db_id('erp') is null exec('create database erp');
go
use erp;
go

drop table if exists dbo.sales_order_lines;
drop table if exists dbo.sales_orders;
drop table if exists dbo.products;
drop table if exists dbo.customers;
go

create table dbo.customers (
  customer_id int primary key,
  name        nvarchar(120) not null,
  tax_id      varchar(20)   null,
  email       nvarchar(160) null,
  country     char(2)       not null,
  segment     varchar(20)   not null,
  loaded_at   date          not null
);

create table dbo.products (
  product_id  int primary key,
  sku         varchar(24)   not null unique,
  name        nvarchar(120) not null,
  category    varchar(40)   not null,
  list_price  decimal(12,2) not null,
  loaded_at   date          not null
);

create table dbo.sales_orders (
  order_id    bigint primary key,
  customer_id int           not null,
  order_date  date          not null,
  currency    char(3)       not null,
  status      varchar(12)   not null,
  net_amount  decimal(14,2) not null,
  loaded_at   date          not null
);

create table dbo.sales_order_lines (
  line_id     bigint primary key,
  order_id    bigint        not null,
  product_id  int           not null,
  quantity    int           not null,
  unit_price  decimal(12,2) not null,
  line_amount decimal(14,2) not null,
  loaded_at   date          not null
);
go

/* A numbers table. master..spt_values is only ~2500 rows, so cross join it. */
with n as (
  select top (200000) row_number() over (order by (select null)) as i
  from master..spt_values a cross join master..spt_values b
)
select i into #n from n;
go

declare @today date = cast(getutcdate() as date);
declare @days  int  = 45;

/* ---- customers: 2 000, spread over the window ------------------------- */
insert into dbo.customers (customer_id, name, tax_id, email, country, segment, loaded_at)
select i,
       concat(N'Müşteri ', i),
       -- 2% have no tax id at all
       case when i % 50 = 0 then null
            else right('0000000000' + cast(i * 7919 % 9999999999 as varchar(10)), 10) end,
       -- 1% are not addresses
       case when i % 100 = 0 then concat(N'musteri', i, N'.example.com')
            else concat(N'musteri', i, N'@example.com') end,
       case i % 5 when 0 then 'TR' when 1 then 'DE' when 2 then 'NL' when 3 then 'US' else 'GB' end,
       case i % 4 when 0 then 'KEY' when 1 then 'MID' when 2 then 'SMB' else 'RETAIL' end,
       dateadd(day, -(i % @days), @today)
from #n where i <= 2000;

/* ---- products: 300 ---------------------------------------------------- */
insert into dbo.products (product_id, sku, name, category, list_price, loaded_at)
select i,
       concat('SKU-', right('00000' + cast(i as varchar(5)), 5)),
       concat(N'Ürün ', i),
       case i % 6 when 0 then 'PALLET' when 1 then 'PARCEL' when 2 then 'CONTAINER'
                  when 3 then 'AIRFREIGHT' when 4 then 'WAREHOUSE' else 'CUSTOMS' end,
       cast(50 + (i * 37 % 4950) as decimal(12,2)),
       dateadd(day, -@days, @today)
from #n where i <= 300;

/* ---- orders: ~60 000, with one thin day ------------------------------- */
;with base as (
  select i,
         -- 0.5% point at a customer that does not exist
         case when i % 200 = 0 then 900000 + i else 1 + (i * 13 % 2000) end as customer_id,
         dateadd(day, -(i % @days), @today) as order_date,
         case when i % 977 = 0 then 'XYZ'                      -- outside the allowed set
              when i % 3 = 0 then 'TRY' when i % 3 = 1 then 'USD' else 'EUR' end as currency,
         case i % 7 when 0 then 'CANCELLED' when 1 then 'OPEN' when 2 then 'SHIPPED'
                    else 'INVOICED' end as status,
         cast(100 + (i * 91 % 49900) as decimal(14,2)) as net_amount
  from #n where i <= 60000
)
insert into dbo.sales_orders (order_id, customer_id, order_date, currency, status, net_amount, loaded_at)
select 100000 + i, customer_id, order_date, currency, status,
       -- cancelled orders should be zero, and mostly are
       case when status = 'CANCELLED' and i % 11 <> 0 then 0 else net_amount end,
       order_date
from base
-- a delivery that mostly failed: keep a third of one day
where order_date <> dateadd(day, -17, @today) or i % 3 = 0;

/* ---- lines: three per order, 1% not adding up ------------------------- */
insert into dbo.sales_order_lines (line_id, order_id, product_id, quantity, unit_price, line_amount, loaded_at)
select o.order_id * 10 + l.n,
       o.order_id,
       1 + ((o.order_id + l.n) * 17 % 300),
       1 + (l.n % 3),
       cast(o.net_amount / 3.0 as decimal(12,2)),
       -- 1% of orders have a line total that disagrees with the header
       case when o.order_id % 100 = 0 and l.n = 1
            then cast(o.net_amount / 3.0 * 1.4 as decimal(14,2))
            else cast(o.net_amount / 3.0 as decimal(14,2)) end,
       o.loaded_at
from dbo.sales_orders o
cross join (select 1 as n union all select 2 union all select 3) l;
go

create index ix_orders_loaded_at on dbo.sales_orders (loaded_at);
create index ix_lines_order      on dbo.sales_order_lines (order_id);
create index ix_customers_loaded on dbo.customers (loaded_at);
go

select 'customers' as t, count(*) as rows from dbo.customers
union all select 'products',   count(*) from dbo.products
union all select 'orders',     count(*) from dbo.sales_orders
union all select 'lines',      count(*) from dbo.sales_order_lines;
go
