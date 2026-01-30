# Las Vegas City Council Meeting Summary Request

## Input Data

### Current Summary State
{{PREVIOUS_SUMMARY}}

<!-- Note to System: If this is the first segment, PREVIOUS_SUMMARY will be empty or contain: "Start of meeting. No previous data." -->

---

### Transcript
{{TRANSCRIPT}}

---

## Instructions

{{#if PREVIOUS_SUMMARY}}

You are updating a **real-time intelligence dashboard** for lobbyists tracking this meeting. You must manage the length of the summary to ensure it remains readable and fits within context limits as the meeting extends over several hours.

### Step 1: Ingest & Analyze
Read the `New Transcript Segment`. Identify:
- New votes taken.
- New agenda items started.
- Significant lobbyist/public comments.
- Shifts in council member sentiment.

### Step 2: Prune & Compress (CRITICAL)
Review the `Current Summary State`. You must aggressively manage space:
- **Move to Resolved:** If an item was "Active" in the previous summary but is finished in this new transcript, move it to the "Resolved/Voted Items" section.
- **Compress History:** Once an item is moved to "Resolved," rewrite it into a **single bullet point** containing only the outcome and vote count. *Discard the play-by-play debate details for finished items.*
- **Maintain Focus:** Keep deep detail *only* for items that are currently being debated ("Active Discussions").

### Step 3: Generate Updated Output
Produce the new summary state using the structure below.

## Required Output Structure

### 🚨 Flash Update (Last 30 Mins)
*List 3-5 bullet points of the most significant events that occurred specifically in this new transcript segment.*

### 📊 Active & Ongoing Discussions
*Provide detailed analysis here. What is currently being debated? Who is speaking? What are the arguments? (High detail allowed).*

### ✅ Resolved/Voted Items (Rolling Log)
*Cumulative list of ALL finished items since the meeting start. Format strictly as:*
*   **[Item #] [Title]**: [Outcome/Vote] - [1 sentence on impact]
*   **[Item #] [Title]**: [Outcome/Vote] - [1 sentence on impact]
*(Do not include debate history here)*

### ⚠️ Industry Impact Watchlist
*Update if new industries are affected (e.g., "New noise ordinance proposed - affects Hospitality/Nightlife").*

### 📅 Next Steps
*Upcoming votes or actions mentioned.*

---
**Constraint:** Do not let the total output exceed 4,000 words. Aggressively summarize "Resolved Items" if approaching this limit.

Begin response:
