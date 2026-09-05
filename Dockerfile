# The contract layer: runner, API and the rule-authoring UI. Nothing is built
# here that pip cannot install, so this stays a single stage.
FROM python:3.12-slim

# datacontract-cli runs the SQL Server checks, and its sqlserver driver is
# pyodbc, which needs Microsoft's ODBC driver present in the image. This is the
# reason the checks run here rather than on someone's laptop.
RUN apt-get update  && apt-get install -y --no-install-recommends curl gnupg ca-certificates  && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg  && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main"       > /etc/apt/sources.list.d/mssql-release.list  && apt-get update  && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY core core
COPY api api
COPY integrations integrations
RUN pip install --no-cache-dir -e ".[odd]"

RUN pip install --no-cache-dir pymongo "datacontract-cli[sqlserver,postgres]"
COPY contracts contracts
COPY seed seed
COPY web web

EXPOSE 8077
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8077"]
