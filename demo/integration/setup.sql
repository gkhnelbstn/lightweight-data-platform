-- Two systems of record for the two-way integration demo (ADR 0021): a CRM and
-- a billing package, on the demo SQL Server, each with its own database,
-- names, types and codes. Both are seeded so the first sync has something to
-- decide: three customers identical in both, one that disagrees, and one on
-- each side only.
--
--   sqlcmd -S localhost -U sa -P ... -C -i demo/integration/setup.sql
SET NOCOUNT ON;
IF DB_ID('crm') IS NULL CREATE DATABASE crm;
IF DB_ID('billing') IS NULL CREATE DATABASE billing;
GO

USE crm;
IF OBJECT_ID('dbo.account') IS NOT NULL DROP TABLE dbo.account;
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
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'crm') = 0
    EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table @source_schema = 'dbo', @source_name = 'account',
     @role_name = NULL, @supports_net_changes = 1;
GO

USE billing;
IF OBJECT_ID('dbo.customer') IS NOT NULL DROP TABLE dbo.customer;
CREATE TABLE dbo.customer (
    CustomerCode int           NOT NULL PRIMARY KEY,
    Name         nvarchar(150) NOT NULL,
    TaxId        varchar(11)   NULL,
    IsActive     bit           NOT NULL DEFAULT 1,
    CreatedAt    datetime2     NOT NULL DEFAULT sysutcdatetime()
);
INSERT INTO dbo.customer (CustomerCode, Name, TaxId, IsActive) VALUES
    (1, N'Anadolu Gıda',   '1234567890', 1),
    (2, N'Boğaz Lojistik', '2345678901', 1),
    (3, N'Çınar Yapı',     '3456789012', 0),
    (4, N'Delta Limited',  '4567890123', 1),
    (6, N'Fırat Enerji',   '6789012345', 1);
IF (SELECT is_cdc_enabled FROM sys.databases WHERE name = 'billing') = 0
    EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table @source_schema = 'dbo', @source_name = 'customer',
     @role_name = NULL, @supports_net_changes = 1;
GO
