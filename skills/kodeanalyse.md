---
name: kodeanalyse
keywords: kodeanalyse, kode, code, python, debug, review, teknisk, arkitektur, analyser, struktur
template: kodeanalyse
action_types: [analyze]
description: Struktureret kodeanalyse med gennemgang af formal, imports, arkitektur, kodekvalitet og sikkerhed.
---
# Kodeanalyse

Nar du analyserer kode:

1. **Formal**: Hvad gør filen/klassen/funktionen?
2. **Imports**: Hvilke biblioteker og afhængigheder bruges?
3. **Arkitektur**: Hvordan er koden struktureret? Monster, klasser, funktioner.
4. **Kodekvalitet**: Lasbarhed, navngivning, DRY, test coverage.
5. **Sikkerhed**: Identificer potentielle sarbarheder — hardcodede credentials, SQL injection, usikrede endpoints.

## Verificering for rapportering (OBLIGATORISK)

Før du påstår at noget er et problem, **verificer det i koden**:

1. **Læs den eksakte linje** du henviser til — gæt ALDRIG linjenumre.
2. **Tæl til den eksakte linje** — linje 1 er forste linje.
3. **Kopier det faktiske indhold** og bekræft at det matcher din påstand.
4. **Hvis du IKKE kan verificere linjen findes**: rapporter den IKKE.
5. **Falske positiver er VÆRRE end manglende issues** — hellere færre korrekte end mange forkerte.

**Kategorier af issues at lede efter:**
- Manglende fejlhandtering (try/except, if exists checks)
- Hardcodede stier eller credentials
- Race conditions / concurrency problemer
- Edge cases der ikke håndteres
- Død kode eller ubrugte imports

**Vigtigt:** Vær konkret — henvis til specifikke linjer og funktioner. Eftervis ALTID med faktisk kodeindhold.

<!-- skillflow:known_failures -->
## Kendte Fejlmønstre

**Intermittent failure på Formål/Imports/Arkitektur/Kodekvalitet/Sikkerhed faserne**
- Symptom: LLM læser funktioner én ad gangen (1 read_location per iteration), bruger alle iterationer på læsning og når aldrig at kalde write_file.
- Fasen fejler med "Manglende påkrævede værktøjer: write_file"
- Fix: **Batch reads** — send FLERE read_location-kald i parallel på én gang. Brug list_symbols først for at se alle funktioner, læs dem så 3-4 ad gangen.
- Skriv så snart du har nok data — du behøver ikke læse ALT før du skriver.
<!-- /skillflow:known_failures -->
