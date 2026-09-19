-- Two systems of record for the two-way integration demo (ADR 0021): a CRM and
-- a billing package, on the demo SQL Server, each with its own database,
-- names, types and codes. Both are seeded so the first sync has something to
-- decide: three customers identical in both, one that disagrees, and one on
-- each side only. The CRM keeps addresses in a table of their own, one row per
-- kind; billing keeps them as two columns of the customer (#79).
--
--   sqlcmd -S localhost -U sa -P ... -C -i demo/integration/setup.sql
SET NOCOUNT ON;
IF DB_ID('crm') IS NULL CREATE DATABASE crm;
IF DB_ID('billing') IS NULL CREATE DATABASE billing;
GO

USE crm;
-- CDC off for the whole database first. Dropping a tracked table is supposed
-- to drop its capture instance and sometimes leaves it behind, bound to
-- nothing, and the next enable then fails with "already exists" while the new
-- table goes untracked -- silently, as CLAUDE.md warns. These databases are
-- the demo's own, so starting from none is safe.
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'crm') = 1
    EXEC sys.sp_cdc_disable_db;
IF OBJECT_ID('dbo.account_address') IS NOT NULL DROP TABLE dbo.account_address;
IF OBJECT_ID('dbo.account') IS NOT NULL DROP TABLE dbo.account;
-- Its own batch: a batch is compiled against the tables as they are, and an
-- INSERT naming a new column fails while the old table still exists.
GO
CREATE TABLE dbo.account (
    ACCOUNT_CODE int           NOT NULL PRIMARY KEY,
    TITLE        nvarchar(100) NOT NULL,
    TAX_NO       varchar(11)   NULL,
    ACTIVE       char(1)       NOT NULL DEFAULT 'Y'
);
INSERT INTO dbo.account VALUES
    (1, N'Anadolu Gıda',   '1234567890', 'Y'),
    (2, N'Boğaz Lojistik', '2345678901', 'Y'),
    (3, N'Çınar Yapı',     '3456789012', 'N'),
    (4, N'Delta Ltd',      '4567890123', 'Y'),
    (5, N'Ege Tekstil',    '5678901234', 'Y');
CREATE TABLE dbo.account_address (
    ACCOUNT_CODE int           NOT NULL,
    ADDR_TYPE    char(3)       NOT NULL,      -- INV invoice, SHP shipping
    CITY         nvarchar(60)  NULL,
    PRIMARY KEY (ACCOUNT_CODE, ADDR_TYPE)
);
INSERT INTO dbo.account_address VALUES
    (1, 'INV', N'İstanbul'), (1, 'SHP', N'Kocaeli'),
    (2, 'INV', N'İzmir'),
    (4, 'INV', N'Ankara');
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'crm') = 0
    EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table @source_schema = 'dbo', @source_name = 'account',
     @role_name = NULL, @supports_net_changes = 1;
EXEC sys.sp_cdc_enable_table @source_schema = 'dbo', @source_name = 'account_address',
     @role_name = NULL, @supports_net_changes = 1;
GO

USE billing;
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'billing') = 1
    EXEC sys.sp_cdc_disable_db;
IF OBJECT_ID('dbo.customer') IS NOT NULL DROP TABLE dbo.customer;
GO
CREATE TABLE dbo.customer (
    CustomerCode int           NOT NULL PRIMARY KEY,
    Name         nvarchar(150) NOT NULL,
    TaxId        varchar(11)   NULL,
    IsActive     bit           NOT NULL DEFAULT 1,
    InvoiceCity  nvarchar(60)  NULL,
    ShippingCity nvarchar(60)  NULL,
    CreatedAt    datetime2     NOT NULL DEFAULT sysutcdatetime()
);
INSERT INTO dbo.customer (CustomerCode, Name, TaxId, IsActive, InvoiceCity, ShippingCity) VALUES
    (1, N'Anadolu Gıda',   '1234567890', 1, N'İstanbul', N'Kocaeli'),
    (2, N'Boğaz Lojistik', '2345678901', 1, N'İzmir',    NULL),
    (3, N'Çınar Yapı',     '3456789012', 0, NULL,        NULL),
    (4, N'Delta Limited',  '4567890123', 1, N'Ankara',   NULL),
    (6, N'Fırat Enerji',   '6789012345', 1, N'Erzurum',  NULL);
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'billing') = 0
    EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table @source_schema = 'dbo', @source_name = 'customer',
     @role_name = NULL, @supports_net_changes = 1;
GO
