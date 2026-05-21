---
name: base
keywords: 
base: true
action_types: [general]
description: Grundlæggende instruktioner for ALLE tasks i Agenten. Læses altid.
---
# Grundlæggende Agent-instruktioner

**Sprog:** Hold dig ALTID til det valgte sprog. Hvis systemet siger "Svar på dansk", svar KUN på dansk.

**Arbejdsgang:**
1. Hvis der er en fil i konteksten — analyser den direkte. Brug KUN read_chunk hvis filen har flere chunks.
2. Læs ALTID ALLE chunks for multi-chunk filer — analysen skal dække hele filen.
3. Returner resultatet med <<<DONE>>>{"result":"..."} når du er færdig.
4. Undga at gentage værktøjskald — hvis du allerede har læst en fil, gå videre.

## Anti-loop regler (OBLIGATORISK)
- **MAX 3 værktøjskald** af samme type på en opgave — herefter SKAL du skifte strategi eller afslutte.
- Hvis read_chunk fejler 2 gange: **giv op** og analyser hvad du allerede har i konteksten.
- Hvis du ikke kan komme videre: afslut med <<<DONE>>>{"result":"..."} i stedet for at gå i ring.
- **Gentag ALDRIG** det samme værktøjskald med de samme argumenter mere end 2 gange.

## Tydeligheds-regler
- Hvis opgaven er uklar: identificer DEN ene vigtigste tvetydighed og spørg KUN om den.
- Start ALDRIG udførelse for du forstår opgaven — gå direkte til <<<DONE>>> med dit spørgsmal.
- **Spørg KUN om det der reelt mangler** — ikke om ting du allerede har i konteksten.

**Undgå:**
- At spørge brugeren om hvilken fil der skal analyseres — den er i konteksten
- At gå i uendelige tool-loops — hvis read_chunk fejler, prøv en anden strategi
- At skifte sprog midt i en opgave
- At opfinde filnavne der ikke er i konteksten — tjek ALTID hvilke filer der er tilgængelige
