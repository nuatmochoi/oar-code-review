FROM golang:1.26.7-bookworm AS go
RUN GOBIN=/out go install golang.org/x/tools/gopls@v0.23.0
COPY vendor/tools/frontier-go.go /src/frontier-go.go
RUN CGO_ENABLED=0 go build -o /out/frontier-go /src/frontier-go.go
FROM mcr.microsoft.com/dotnet/sdk:10.0-noble AS dotnet
RUN dotnet tool install --tool-path /csharp csharp-ls --version 0.20.0
FROM eclipse-temurin:21-jdk-noble AS jdk
FROM rust:1.91.0-slim-bookworm AS rust
RUN rustup component add rust-src rust-analyzer
FROM node:22-bookworm-slim AS node
FROM swift:6.2-noble
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 DOTNET_ROOT=/opt/dotnet DOTNET_ROLL_FORWARD=Major DOTNET_CLI_TELEMETRY_OPTOUT=1 GOTELEMETRY=off
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv nodejs npm git jq curl unzip ruby ruby-dev build-essential libicu74 libssl3t64 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir tiktoken==0.12.0 jsonschema==4.25.1 tree-sitter==0.25.2 tree-sitter-language-pack==0.10.0
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN npm install -g pyright@1.1.414 typescript@5.9.3 typescript-language-server@5.1.3 bash-language-server@5.6.0 intelephense@1.16.1
RUN gem install solargraph --version 0.56.0 --no-document
COPY --from=go /usr/local/go /usr/local/go
COPY --from=go /out/gopls /usr/local/bin/gopls
COPY --from=dotnet /usr/share/dotnet /opt/dotnet
COPY --from=dotnet /csharp /opt/csharp
COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
ENV PATH=/opt/venv/bin:/usr/local/go/bin:/opt/csharp:/opt/dotnet:/opt/java/openjdk/bin:/usr/local/cargo/bin:$PATH JAVA_HOME=/opt/java/openjdk RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
COPY vendor /opt/agent/vendor
COPY --from=go /out/frontier-go /opt/agent/vendor/tools/frontier-go
COPY product /opt/agent/product
COPY *.py /opt/agent/
ENV PYTHONPATH=/opt/agent/vendor:/opt/agent/product:/opt/agent TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN mkdir -p /opt/tiktoken && python -c 'import tiktoken; tiktoken.get_encoding("o200k_base")'
RUN python /opt/agent/install_java.py
WORKDIR /work/repo
ENTRYPOINT ["python","-B","/opt/agent/entrypoint.py"]
