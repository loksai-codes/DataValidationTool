# Container image for running dvtool in CI/CD or as a scheduled job (ECS, EKS, Alibaba ACK/SAE ...).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[all]" && useradd --create-home dv
USER dv
WORKDIR /work

# Mount your project file and pass secrets as environment variables, e.g.
#   docker run --rm -v "$PWD:/work" -e AWS_BILLING_PASSWORD=... dvtool validate -c project.yaml
ENTRYPOINT ["dvtool"]
CMD ["--help"]
