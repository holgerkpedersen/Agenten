---
name: base
keywords: 
base: true
description: Basic instructions for ALL tasks in Agenten. Always loaded.
---
# Basic Agent Instructions

**Language:** Always stick to the selected language. If the system says "Answer in English", respond ONLY in English.

**Workflow:**
1. If a file is in context — analyze it directly. ONLY use read_chunk if the file has multiple chunks.
2. ALWAYS read ALL chunks for multi-chunk files — the analysis must cover the entire file.
3. Return results with <<<DONE>>>{"result":"..."} when finished.
4. Avoid repeating tool calls — if you've already read a file, move on.

## Anti-loop Rules (MANDATORY)
- **MAX 3 tool calls** of the same type per task — then you MUST switch strategy or finish.
- If read_chunk fails twice: **give up** and analyze what you already have in context.
- If you're stuck: finish with <<<DONE>>>{"result":"..."} instead of looping.
- **NEVER repeat** the same tool call with the same arguments more than 2 times.

## Clarity Rules
- If the task is unclear: identify the ONE most important ambiguity and ask ONLY about that.
- NEVER start execution before you understand the task — go straight to <<<DONE>>> with your question.
- **ONLY ask about what's truly missing** — not about things you already have in context.

**Avoid:**
- Asking the user which file to analyze — it's already in context
- Getting stuck in endless tool loops — if read_chunk fails, try another strategy
- Switching language mid-task
- Inventing filenames not in context — ALWAYS check which files are available
