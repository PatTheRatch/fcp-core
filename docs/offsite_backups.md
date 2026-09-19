# Off-site backups to S3

**Written:** 2026-09-19. **Status:** built; switched on once the four
settings below are in the VPS `.env`.

The nightly dump (`scripts/backup_db.sh`, 10:00 UTC) is verified and kept for
fourteen days, but on the same disk as the database. A lost VPS loses both.
After each verified dump, `scripts/offsite_backup.py` copies it to an S3
bucket, encrypted at rest, reads the object's size back and refuses to call
the copy good unless it matches. The watchdog (11:00 UTC) says so when the
last good copy is more than 36 hours old.

## What Patrick does in AWS, once

1. **A bucket.** S3, Create bucket, a name of your choosing (for example
   `fcp-core-backups-pm`), the region closest to the VPS (the VPS is in
   Nuremberg, so `eu-central-1`, Frankfurt). Leave **Block all public access**
   on. Default encryption: SSE-S3 is fine.
2. **Retention.** In the bucket, Management, Create lifecycle rule: name it
   `expire-old-backups`, limit it to the prefix `fcp-core/backups/`, and
   "Expire current versions of objects" after **90 days**. At about 16 MB a
   night now (more in season, as the listener's snapshots grow) that holds
   ninety copies, about 1.5 GB.
3. **A user that can only do this.** IAM, Users, Create user
   (`fcp-core-backup`), no console access. Attach an inline policy, with the
   bucket name in place of `BUCKET`:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "WriteAndReadBackups",
         "Effect": "Allow",
         "Action": ["s3:PutObject", "s3:GetObject"],
         "Resource": "arn:aws:s3:::BUCKET/fcp-core/backups/*"
       },
       {
         "Sid": "ListBackups",
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::BUCKET",
         "Condition": { "StringLike": { "s3:prefix": "fcp-core/backups/*" } }
       }
     ]
   }
   ```

   It cannot delete anything, so a stolen key cannot wipe the backups;
   the lifecycle rule does the deleting.
4. **An access key** for that user (Security credentials, Create access key,
   "Application running outside AWS"). Copy both halves once.

## What goes in the VPS `.env`

Put these in `/opt/fcp-core/.env` yourself, in a terminal on the VPS, not
through a chat:

```
FCP_S3_BUCKET=fcp-core-backups-pm
FCP_S3_REGION=eu-central-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

`FCP_S3_PREFIX` defaults to `fcp-core/backups/`, matching the policy.

Then prove it, from `/opt/fcp-core`:

```
PYTHONPATH=. .venv/bin/python scripts/offsite_backup.py
PYTHONPATH=. .venv/bin/python scripts/offsite_backup.py --check
```

The first copies the newest dump and prints where it went; the second lists
what the bucket holds. From then on the nightly backup does it.

## Restoring from S3

On any machine with the AWS CLI and the same key (or a key with read access):

```
aws s3 ls s3://BUCKET/fcp-core/backups/ --recursive | tail -5
aws s3 cp s3://BUCKET/fcp-core/backups/2026/09/fcp-20260919T100026Z.dump .
pg_restore --clean --if-exists --no-owner -d "postgresql://fcp:...@localhost:5433/fcp" fcp-20260919T100026Z.dump
```

`pg_restore --list` on the file first shows it is whole. The dump carries
every table, including the sealed ESPN logins; those stay unreadable without
`FCP_SECRETS_KEY`, which is deliberately **not** in the backup. Keep that key
somewhere of its own (a password manager), or a restored database will need
every league reconnected.

## What it costs

A 16 MB dump a night, ninety kept, is about 1.5 GB, growing through the
season: well inside S3's free allowance while it lasts, and a few US cents a
month in S3 Standard after it.
