# The contract layer: runner, API and the rule-authoring UI. Nothing is built
# here that pip cannot install, so this stays a single stage.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY core core
COPY api api
COPY integrations integrations
RUN pip install --no-cache-dir -e ".[odd]"

COPY contracts contracts
COPY seed seed
COPY web web

EXPOSE 8077
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8077"]
