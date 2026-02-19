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

# How to SSH into EC2

First change user to root

```bash
sudo -i
```

Then change it to ubuntu user

```bash
su - ubuntu
```

# Follow the Logs in Real-Time in EC2

To see all previous logs and then keep the terminal open to see new lines as they happen, combine the flags:

```bash
journalctl -u council-recorder -f
```

(Add -n 100 if you want to see the 100 lines leading up to the current moment before it starts "following" the live stream).