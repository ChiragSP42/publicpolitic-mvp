# The Data Model (DynamoDB Table):

Table name: Council Meetings
Partition Key: video ID

Item structure (what a record would look like):

```JSON
{
  "video_id": "dQw4w9WgXcQ",          // Partition Key
  "status": "ACTIVE",                 // ACTIVE, COMPLETED, or ERROR
  "start_time": "2024-01-28T10:00:00Z",
  "last_checkpoint_index": 450,       // For the Historian to track progress
  "summary": ""                      // The summary for the Frontend
}
```
