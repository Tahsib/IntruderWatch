# Contributing to IntruderWatch

Thank you for your interest in contributing to IntruderWatch! We welcome all types of contributions, from bug fixes and documentation improvements to performance optimizations across hardware backends.

## Getting Started

1. **Fork the Repository**: Create your own copy of the project on GitHub.
2. **Clone the Fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/IntruderWatch.git
   cd IntruderWatch
   ```
3. **Set Up Development Environment**:
   - Install **Go 1.22+**
   - Install **Python 3.12+**
   - Install **Docker & Docker Compose v2+**
   - (Optional) Install AMD ROCm or NVIDIA CUDA drivers if testing GPU-accelerated inference.
4. **Create a Feature Branch**:
   ```bash
   git checkout -b feat/your-feature-name
   ```

## Development Standards

- **Code Style & Linting**:
  - **Python**: Formatted and linted with **Ruff** (`ruff check .`, `ruff format --check .`).
  - **Go**: Formatted with standard `gofmt` and verified with `go vet ./...`.
  - **Docker**: Linted with **Hadolint** for security and best practices.
- **Microservices Architecture**: Maintain strict decoupling between video ingestion (`frame_capturer`), detection inference (`human_detector`), notification dispatch (`alert_service`), and web audit (`viewer_service`).
- **Commit Messages**: We strictly follow [Conventional Commits](https://www.conventionalcommits.org/) format (e.g., `feat:`, `fix:`, `perf:`, `docs:`, `chore:`). Write concise one-liner commit messages.

## Testing & Quality Gates

Before submitting a Pull Request, ensure all tests and pre-commit checks pass:

1. **Go Unit Tests**:
   ```bash
   cd microservices/frame_capturer
   go test -v -race ./...
   ```
2. **Python Quality**:
   ```bash
   ruff check .
   ruff format --check .
   ```
3. **Pre-commit Hooks**:
   ```bash
   pre-commit run --all-files
   ```
4. **Integration Validation**:
   ```bash
   cd microservices
   cp .env.example .env
   docker compose config --quiet
   ```

## Submitting Pull Requests

1. Push your commits to your fork.
2. Open a Pull Request targeting the `main` branch.
3. Ensure CI quality gates pass. Pull requests are merged using the **Rebase** merge strategy to preserve a clean, linear git history.

## License

By contributing to IntruderWatch, you agree that your contributions will be licensed under the project's **GNU AGPLv3 License**.
