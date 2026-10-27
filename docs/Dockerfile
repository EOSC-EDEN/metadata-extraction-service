# Documentation site. Build context: the repository root.
#   docker build -f docs/Dockerfile -t eosc-mes-docs .
FROM python:3.12-slim AS build
WORKDIR /src
COPY docs/requirements.txt docs/requirements.txt
RUN pip install --no-cache-dir -r docs/requirements.txt
COPY mkdocs.yml .
COPY docs docs
RUN NO_MKDOCS_2_WARNING=1 mkdocs build --strict

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /src/site /usr/share/nginx/html
EXPOSE 8080
