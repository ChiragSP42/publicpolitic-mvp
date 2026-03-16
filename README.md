# Politic Public: Phase II - Architecture & KT Document

## 1. Project Context

This document outlines the Phase II architecture for Politic Public's platform, designed to democratize access to local government proceedings by making council meetings searchable, analyzable, and actionable. Moving past the initial Proof of Concept (PoC) , this Minimum Viable Product (MVP) implements real-time transcript generation, rolling summarizations, live agenda tracking, and an enhanced Retrieval-Augmented Generation (RAG) chatbot using Amazon Bedrock.

## 2. High-Level Architecture Flow

While the initial Statement of Work proposed standard AWS managed services (like Amazon Transcribe and OpenSearch), the implemented architecture utilizes a more cost-effective, custom-built serverless orchestration model using Step Functions, EC2, and Bedrock Knowledge Bases.

**The Meeting Lifecycle:**

1. **EventBridge (Cron)** triggers the **Scout Lambda** on specific meeting days.
2. **Scout Lambda** checks YouTube for a live stream, uses AI to fetch the planned agenda, logs the meeting to DynamoDB, and starts the **Step Function**.
3. **Step Function Orchestrator** boots up the **EC2 Soldier**.
4. **EC2 Soldier** streams audio via proxy, runs local `faster-whisper` transcription, and pushes chunks to S3.
5. Every 15 minutes, the **Step Function** triggers the **Historian Lambda**.
6. **Historian Lambda** reads new S3 transcripts, updates the rolling summary via Claude 3.5 Sonnet, generates a live agenda tracker, and updates DynamoDB.
7. When the stream ends, the Soldier marks DynamoDB as `COMPLETED`. The Historian sees this, does one final summary, and tells the Step Function to stop the EC2 instance.

---

## 3. Component Deep Dive

### A. The Scout (`scout_lambda.py`)

* **Trigger:** AWS EventBridge explicitly scheduled for Las Vegas Council meeting times (adjusted for UTC).
* **Role:** The initiator. It checks the YouTube API for a live video. To avoid the YouTube API `quotaExceeded` trap, it only runs during expected meeting windows rather than aggressive 24/7 polling.
* **Agenda Extraction:** Uses the `tavily-python` SDK to search the web for the official PrimeGov PDF agenda. We pivoted to Tavily because using Playwright/Headless Chromium inside Lambda bloated the deployment package and caused OS-level shared library crashes. Tavily fetches the PDF URL, and `pypdf` extracts the raw text (doesn't work, but code exists if you want to continue working on it).
* **Idempotency:** Checks DynamoDB before starting. If the meeting is already `ACTIVE`, it gracefully exits to prevent overlapping workflows.

### B. The Orchestrator (AWS Step Functions)

* **Role:** The conductor. It replaces the need for EC2 to manage its own lifecycle.
* **Workflow:** `Start EC2` ➡️ `Wait 15 Mins` ➡️ `Invoke Historian` ➡️ `Choice Gate`.
* **State Management:** Passes a JSON payload (`{"video_id": "...", "meeting_active": true}`) between states. By using `resultPath: sfn.JsonPath.DISCARD` on the EC2 task, the original payload survives the AWS API responses, acting as a secure "backpack" of state without relying on global SSM variables that could cause race conditions during overlapping meetings.

### C. The EC2 Soldier (`ec2_soldier_code.py`)

* **Role:** The heavy lifter. A `t3.medium` instance running `faster-whisper`.
* **Boot Sequence:** The CDK UserData script installs dependencies and configures `systemd`. During the initial `cdk deploy`, it automatically runs `shutdown -h now` immediately after setup to save costs. When the Step Function starts the instance later, `systemd` automatically launches the Python script.
* **Data Stream:** Uses `yt-dlp` to get the stream URL, routed through an IPRoyal proxy to prevent YouTube IP blocking. Streams audio via `ffmpeg` directly into `faster-whisper` using `np.frombuffer`.
* **S3 Routing:** Splits the output. Raw JSON goes to `/app-data/` (for UI consumption) and plain text goes to `/knowledge-base/` (for Bedrock RAG) to prevent duplicate vector indexing.

### D. The Historian (`historian_lambda.py`)

* **Role:** The synthesizer. Runs every 15 minutes.
* **LLM Integration:** Uses the Cross-Region Inference Profile for Claude 3.5 Sonnet (`us.anthropic.claude-sonnet...`) to ensure high availability and route around regional throttling.
* **Rolling Summary:** Pulls the previous summary from DynamoDB and appends the new 15-minute transcript chunk to generate a cohesive, running document.
* **Live Agenda Tracker:** Cross-references the official planned agenda (found by Scout) with the rolling summary to generate a live `COMPLETED / IN PROGRESS / UPCOMING` status tracker.

### E. The Chatbot (`chatbot_lambda.py`)

* **Role:** The customer-facing RAG API.
* **Knowledge Base:** Leverages Amazon Bedrock Knowledge Bases (backed by OpenSearch Serverless).
* **Filtering:** When a user asks a question via the frontend, the Lambda passes a `date_filter` to Bedrock. Bedrock uses metadata filtering to restrict the vector search *only* to the transcript chunks from that specific meeting date, preventing cross-contamination of answers.

---

## 4. Infrastructure & Database (CDK)

### DynamoDB Schema Constraints

Because AWS Amplify Gen 2 automatically tries to assign a hidden `id` field as the partition key, you must explicitly enforce the `videoId` as the identifier in your frontend schema to prevent Boto3 `ValidationException` errors:

```typescript
  Summary: a.model({
    videoId: a.string().required(),
    // ... other fields
  }).identifier(['videoId']) 

```

### IAM & Security

* **Least Privilege:** All roles are strictly scoped. The Chatbot Lambda is granted specific access to both `inference-profile/*` and `*::foundation-model/*` to accommodate Bedrock's cross-region routing quirks.
* **Networking:** The VPC uses only Public Subnets. This allows the EC2 instance to communicate with YouTube and AWS APIs without incurring the high hourly costs of a NAT Gateway.

---

## 5. Deployment Prerequisites

To deploy this stack successfully, your `.env` file at the root of the CDK project must contain:

```text
YOUTUBE_API_KEY=your_google_cloud_api_key
CHANNEL_ID=your_target_youtube_channel_id
PROXY_USER=iproyal_username
PROXY_PASS_BASE=iproyal_password_base
BUCKET_NAME=your_amplify_s3_bucket_name
TABLE_NAME=your_amplify_dynamodb_table_name
KNOWLEDGE_BASE_ID=your_bedrock_kb_id
TAVILY_API_KEY=tvly-your_tavily_key

```

**To Deploy:**

```bash
npx aws-cdk deploy

```

---

## 6. Known "Gotchas" & Future Maintenance

1. **EventBridge Timezones:** EventBridge strictly operates in UTC. Las Vegas is PT. The CDK cron expressions currently account for PDT (UTC-7). When Daylight Saving Time ends (PST / UTC-8), the cron hours in `infra-stack.ts` must be shifted by +1 hour.
2. **Lambda Python Versions:** The Dockerfiles for the Lambdas are built using the AWS Python base image. If you add new dependencies, ensure they do not conflict with the base Python version (e.g., the `collections.Sequence` deprecation in Python 3.10+).
3. **EC2 Scaling:** Currently, the architecture supports one meeting at a time via a single EC2 instance. If the city council begins running overlapping simultaneous streams, the EC2 logic will need to be containerized and migrated to AWS ECS Fargate to scale horizontally.