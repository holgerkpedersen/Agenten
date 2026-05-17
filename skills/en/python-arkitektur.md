---
name: python-arkitektur
keywords: python, flask, architecture, design, structure, module, component, web, api, rest, database, sqlalchemy, html, javascript, frontend, backend, blueprint, jinja, jwt, oauth, docker, deploy, ci, cd
template: python-arkitektur
description: Planning and documentation of Python/Flask project architecture with web development best practices.
---

# Python Architecture — Best Practices

## System Structure
- **Layered architecture**: Presentation (HTML/JS/Jinja2) → Business Logic (Flask blueprints, services) → Data (SQLAlchemy, repositories)
- **Modular design**: Each blueprint owns its module with routes, models, services
- **Separation of concerns**: Never mix logic across layers

## Flask
- Use **blueprints** to organize routes by domain
- **Application factory** pattern for testability and configuration
- **Middleware**: before_request/after_request for logging, rate limiting, CORS
- **Error handling**: Custom error handlers (400, 401, 403, 404, 500) in blueprint or app
- **Configuration**: Environment-based (dev/test/prod) via environment variables

## Database
- **SQLAlchemy ORM** with declarative models
- **Alembic** for migrations with auto-generation
- **Indexes** on foreign keys and frequently queried columns
- **Relationships**: lazy='selectin' or joined loading for N+1 prevention
- **Session management**: scoped session per request

## Security
- **CSRF**: Flask-WTF or manual token validation on POST/PUT/DELETE
- **XSS**: Jinja2 auto-escaping, avoid |safe with user data
- **SQL injection**: SQLAlchemy parameterized queries (never raw SQL with f-strings)
- **Authentication**: Flask-Login (session-based) or JWT (API)
- **Authorization**: @login_required decorator, role-based access control
- **Secrets**: Environment variables (.env), never hardcoded
- **Rate limiting**: Flask-Limiter extensions

## Frontend
- **Jinja2 templates**: Template inheritance (base.html → child templates), macros for repeated UI elements
- **Static files**: Organized in static/css/, static/js/, static/images/
- **JavaScript**: Modular (ES6 modules or IIFE), avoid inline scripts
- **API communication**: fetch() with JSON, handle loading/error states
- **Form validation**: Both client-side (HTML5/JS) and server-side (WTForms)

## Development Workflow
- **Virtual environment**: venv or poetry
- **Dependencies**: requirements.txt (prod) + requirements-dev.txt
- **Testing**: pytest with fixtures, coverage, unittest for isolated tests
- **Code quality**: flake8 (linting), black (formatting), mypy (type hints), isort (imports)
- **Type hints**: Annotate functions and methods with the typing module
- **Docstrings**: Google-style or NumPy-style for modules, classes, functions

## Deployment
- **WSGI**: Gunicorn (prod), Flask's dev server (dev)
- **Docker**: Multi-stage builds, slim images
- **CI/CD**: GitHub Actions or GitLab CI for test + deploy
- **Environment variables**: python-dotenv for local development
