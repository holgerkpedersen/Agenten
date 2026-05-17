---
name: programmering
keywords: programmer, kode, program, implementer, opret, byg, skriv, kod, kodning, create, app, application, system, develop, development, feature, funktion, build, lav, tool, værktøj, modul, klasse, library, bibliotek
template: programmering
description: Arkitektonisk analyse og planlægning af programmeringsopgaver med kravanalyse, design, implementeringsplan, sikkerhed og kodeimplementering.
---

# Programmeringsopgave — Arkitektonisk analyse

Når du planlægger en programmeringsopgave:

## 1. Kravanalyse
- Identificér funktionelle krav: hvad skal systemet kunne?
- Identificér ikke-funktionelle krav: performance, sikkerhed, skalerbarhed
- Afdæk input/output, brugertilfælde og edge cases
- Notér eventuelle begrænsninger (platform, sprog, tredjepart)

## 2. Arkitekturdesign
- Design komponentopdeling og modulstruktur
- Definér dataflow og grænseflader mellem komponenter
- Overvej relevante design patterns (Factory, Singleton, Observer, etc.)
- Følg SOLID-principper og separation of concerns
- Beskriv arkitekturen klart før du skriver kode

## 3. Implementeringsplan
- Liste over filer der skal oprettes med formål for hver
- Implementeringsrækkefølge (afhængigheder først)
- Teststrategi: enhedstest, integrationstest
- Håndtering af edge cases og fejlsituationer

## 4. Sikkerhedsanalyse
- Følg OWASP best practices
- Inputvalidering og sanitization
- Sikker håndtering af credentials, nøgler og passwords
- Kryptering af følsomme data (at rest og in transit)
- Princip om mindste rettighed (least privilege)
- Logning og audit trails

## 5. Kodeimplementering
- Skriv ren, vedligeholdelsesvenlig kode
- Brug korrekt fejlhåndtering (try/except, error codes)
- Dokumentér formål med klasser og funktioner
- Følg sprogets konventioner og projektets eksisterende stil
