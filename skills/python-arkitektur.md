---
name: python-arkitektur
keywords: python, flask, arkitektur, arkitekt, design, struktur, modul, komponent, web, api, rest, database, sqlalchemy, html, javascript, frontend, backend, blueprint, jinja, jwt, oauth, docker, deploy, ci, cd
action_types: [analyze, write]
template: python-arkitektur
description: Planlægning og dokumentation af Python/Flask-projektarkitektur med best practices for webudvikling.
---

# Python Arkitektur — Best Practices

## Systemstruktur
- **Lagdelt arkitektur**: Præsentation (HTML/JS/Jinja2) → Forretningslogik (Flask blueprints, services) → Data (SQLAlchemy, repositories)
- **Modulopdeling**: Hvert blueprint har sit eget modul med routes, models, services
- **Separation of concerns**: Undgå at blande logik på tværs af lag

## Flask
- Brug **blueprints** til at organisere routes pr. domæne
- **Application factory** pattern til testbarhed og konfiguration
- **Middleware**: before_request/after_request til logging, rate limiting, CORS
- **Fejlhåndtering**: Custom error handlers (400, 401, 403, 404, 500) i blueprint eller app
- **Konfiguration**: Miljøbaseret (dev/test/prod) via environment variables

## Database
- **SQLAlchemy ORM** med deklarative modeller
- **Alembic** til migrationer med auto-generation
- **Indekser** på foreign keys og ofte forespurgte kolonner
- **Relationships**: lazy='selectin' eller joined loading for N+1 prevention
- **Session management**: scoped session per request

## Sikkerhed
- **CSRF**: Flask-WTF eller manuel token-validering på POST/PUT/DELETE
- **XSS**: Jinja2 auto-escape, undgå |safe med brugerdata
- **SQL injection**: SQLAlchemy parameterized queries (aldrig raw SQL med f-strings)
- **Autentifikation**: Flask-Login (session-based) eller JWT (API)
- **Autorisation**: @login_required decorator, role-based access control
- **Secrets**: Miljøvariabler (.env), aldrig hardcoded
- **Rate limiting**: Flask-Limiter extensions

## Frontend
- **Jinja2 templates**: Template inheritance (base.html → child templates), macros til gentagne UI-elementer
- **Statiske filer**: Organiseret i static/css/, static/js/, static/images/
- **JavaScript**: Modulær (ES6 modules eller IIFE), undgå inline scripts
- **API-kommunikation**: fetch() med JSON, håndter loading/error states
- **Formvalidering**: Både client-side (HTML5/JS) og server-side (WTForms)

## Udviklings-workflow
- **Virtuelt miljø**: venv eller poetry
- **Afhængigheder**: requirements.txt (prod) + requirements-dev.txt
- **Testing**: pytest med fixtures, coverage, unittest for isolated tests
- **Kodekvalitet**: flake8 (linting), black (formattering), mypy (type hints), isort (imports)
- **Type hints**: Annotér funktioner og metoder med typing-modulet
- **Docstrings**: Google-style eller NumPy-style for moduler, klasser, funktioner

## Deployment
- **WSGI**: Gunicorn (prod), Flask's dev server (dev)
- **Docker**: Multi-stage builds, slim images
- **CI/CD**: GitHub Actions eller GitLab CI til test + deploy
- **Miljøvariabler**: python-dotenv til lokal udvikling
