import subprocess
from pathlib import Path
import re

import click
from click import Path as PArg


timestamp_re = re.compile(r'^\d\d\d\d-\d\d-\d\d-\d\d:\d\d$')


# Here largely to make mocking easier.
def run_btrfs(args: list[str]) -> int:
    return subprocess.call(['btrfs'] + args,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)


def check_is_subvolume(path: Path, underscore_allowed: bool = False) -> bool:
    if not underscore_allowed and path.name.startswith('.'):
        raise ValueError(f"Subvolume {path} starts with reserved '_' "
                         "character")
    return run_btrfs(['subvolume', 'show', str(path)]) == 0


def check_has_btrfs_tools():
    try:
        result = run_btrfs(['--help'])
    except FileNotFoundError:
        return False
    return result == 0


@click.command()
@click.argument(
    "backup_linkdir",
    type=PArg(exists=True, dir_okay=True, file_okay=False, path_type=Path)
)
@click.argument(
    "snapshot_library",
    type=PArg(exists=True, dir_okay=True, file_okay=False, path_type=Path)
)
@click.argument(
    "backup_library",
    type=PArg(exists=True, dir_okay=True, file_okay=False, path_type=Path)
)
def main(backup_linkdir: Path, snapshot_library: Path, backup_library: Path):
    """
    Backup subvolumes symlinked to in BACKUP_LINKDIR to a subdirectory in
    BACKUP_LIBRARY using subdirectories in SNAPSHOT_LIBRARY to allow
    incremental backups.

    BACKUP_LINKDIR is a directory with relative symlinks to the subvolumes
     to be backed up.

    SNAPSHOT_LIBRARY is a directory with subdirectories of the form
     YYYY-MM-DD-HH:MM, each containing snapshots of all of the subvolumes
     taken at the given time.

    BACKUP_LIBRARY is a directory with subdirectories of the form
     YYYY-MM-DD-HH:MM, each containing the backups of the subvolume snapshots.
    """
    if not check_has_btrfs_tools():
        raise click.ClickException("btrfs command is not installed, it's "
                                   "usually in the btrfs-progs package")
    for backup_link in backup_linkdir.iterdir():
        if backup_link.name.startswith('_'):
            raise click.BadParameter(f'Backup link {backup_link.name} '
                                     'starts with underscore character, names '
                                     'starting with underscores are reserved')
        if not backup_link.is_symlink():
            raise click.BadParameter(f"{backup_link} is not a symlink")
        elif not str(backup_link.readlink()).startswith('../'):
            raise click.BadParameter(f"{backup_link} is not a relative "
                                     "symlink to a subvolume to be backed up")
        elif not check_is_subvolume(backup_link):
            raise click.BadParameter(f"{backup_link} is not a link to a "
                                     "subvolume")


if __name__ == "__main__":
    main()
