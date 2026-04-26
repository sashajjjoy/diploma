import gzip
import io
import os
from datetime import datetime

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

from bookings.models import BackupArchive


BACKUP_APP_LABELS = [
    "auth.user",
    "bookings",
]


def create_backup_archive(*, user):
    buffer = io.StringIO()
    call_command(
        "dumpdata",
        *BACKUP_APP_LABELS,
        indent=2,
        use_natural_foreign_keys=True,
        use_natural_primary_keys=True,
        stdout=buffer,
    )
    json_bytes = buffer.getvalue().encode("utf-8")
    gz_bytes = gzip.compress(json_bytes)
    stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{stamp}.json.gz"
    archive = BackupArchive(created_by=user, original_name=filename)
    archive.file.save(filename, ContentFile(gz_bytes), save=False)
    archive.save()
    return archive


def restore_backup_archive(*, archive, user):
    archive.file.open("rb")
    raw_bytes = archive.file.read()
    archive.file.close()
    json_bytes = gzip.decompress(raw_bytes)
    temp_name = f"restore_{timezone.now().timestamp():.0f}.json"
    temp_path = os.path.join(os.path.dirname(archive.file.path), temp_name)
    with open(temp_path, "wb") as temp_file:
        temp_file.write(json_bytes)
    try:
        call_command("loaddata", temp_path, verbosity=0)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    archive.last_restored_at = timezone.now()
    archive.restored_by = user
    archive.restore_count += 1
    archive.save(update_fields=["last_restored_at", "restored_by", "restore_count"])
    return archive
