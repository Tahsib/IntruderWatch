# Contributing to IntruderWatch

Thank you for your interest in contributing to IntruderWatch! We welcome all types of contributions, from bug fixes to performance optimizations for various hardware configurations.

## 🚀 Getting Started

1. **Fork the Repository**: Create your own copy of the project on GitHub.
2. **Clone the Fork**: `git clone https://github.com/YOUR_USERNAME/IntruderWatch.git`
3. **Set Up Environment**:
   - Install Python 3.12+
   - Install Docker & Docker Compose
   - (Optional) Install AMD ROCm drivers if you plan to test GPU acceleration.
4. **Create a Branch**: `git checkout -b feature/your-awesome-feature`

## 🛠️ Development Standards

- **Code Style**: We use **Ruff** for linting. Please ensure your code passes the lint checks.
- **Microservices Architecture**: Maintain the separation of concerns between capturers, detectors, and alert services.
- **Commit Messages**: We follow the [Conventional Commits](https://www.conventionalcommits.org/) standard (e.g., `feat:`, `fix:`, `perf:`, `docs:`).

## 🧪 Testing

- Before submitting a PR, ensure your changes work in the containerized environment.
- Run `docker compose up --build` in the `microservices/` directory.

## 📥 Submitting Changes

1. Push your changes to your fork.
2. Open a Pull Request against our `main` branch.
3. Provide a clear description of the problem you're solving or the feature you're adding.

## 🛡️ License

By contributing, you agree that your contributions will be licensed under the project's **GNU AGPLv3 License**.
