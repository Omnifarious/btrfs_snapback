import datetime as dt
import os
import subprocess
from pathlib import Path
import re
from typing import Iterable

import click
from click import Path as PArg


timestamp_re = re.compile(r'^\d\d\d\d-\d\d-\d\d-\d\d:\d\d$')
timestamp_format = '%Y-%m-%d-%H:%M'


# Here largely to make mocking easier.
def run_btrfs(args: list[str]) -> int:
    return subprocess.call(['btrfs'] + args,
                           stdout=subprocess.DEVNULL)


def as_timestamp_snapshot(path: Path) -> dt.datetime:
    if not path.is_dir():
        raise ValueError(f'Not a directory: {path}')
    if timestamp_re.match(path.name) is None:
        raise ValueError('Not formatted as a YYYY-MM-DD-HH:MM timestamp: '
                         f'{path}')
    if not all(check_is_subvolume(x) for x in path.iterdir()):
        raise ValueError(f'Not all files in {path} are subvolumes')
    return dt.datetime.strptime(path.name, timestamp_format)


def inventory_library(library: Path) -> dict[dt.datetime, Path]:
    inventory: dict[dt.datetime, Path] = dict()
    to_snaphsot_map = (
        (as_timestamp_snapshot(x), x) for x in library.iterdir()
    )
    for timestamp, path in to_snaphsot_map:
        if timestamp in inventory:
            raise click.BadParameter(f"{library} contains two equivalent "
                                     "timestamps.")
        else:
            inventory[timestamp] = path
    return inventory


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


def full_backup(
        pv_exists: bool, sources: Iterable[Path], backup_snaps: Path
) -> None:
    # cd todays_snaps
    # btrfs send -e * | pv -ar | btrfs recv backup_snaps
    btrfs_args = ['btrfs', 'send', '-e']
    btrfs_args.extend(str(s) for s in sources)
    # If we didn't add any volumes to backup, then we're done!
    if len(btrfs_args) <= 3:
        return
    btrfs_send = subprocess.Popen(
        btrfs_args,
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    if pv_exists:
        middle = subprocess.Popen(
            ['pv', '-r', '-B', str(4 * 2**20)],
            stdin=btrfs_send.stdout,
            stdout=subprocess.PIPE
        )
    else:
        middle = btrfs_send
    btrfs_receive = subprocess.Popen(
        ['btrfs', 'receive', '-e', str(backup_snaps)],
        stdin=middle.stdout,
        stdout=subprocess.DEVNULL
    )
    success = True
    btrfs_receive.wait()
    success = success and btrfs_receive.returncode == 0
    middle.wait()
    success = success and middle.returncode == 0
    if middle is not btrfs_send:
        btrfs_send.wait()
        success = success and btrfs_send.returncode == 0
    if not success:
        raise RuntimeError(f'Btrfs failed to backup {btrfs_args[3:]!r} '
                           f'to {backup_snaps}')


def single_incremental_backup(
        pv_exists: bool, snap_path: Path, parent_path: Path, backup_snaps: Path
) -> None:
    btrfs_cmd = ['btrfs', 'send', '-p', str(parent_path), str(snap_path)]
    btrfs_send = subprocess.Popen(
        btrfs_cmd,
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    if pv_exists:
        middle = subprocess.Popen(
            ['pv', '-r', '-B', str(4 * 2**20)],
            stdin=btrfs_send.stdout,
            stdout=subprocess.PIPE
        )
    else:
        middle = btrfs_send
    btrfs_cmd = ['btrfs', 'receive', str(backup_snaps)]
    btrfs_receive = subprocess.Popen(
        btrfs_cmd,
        stdin=middle.stdout,
        stdout=subprocess.DEVNULL
    )
    success = True
    btrfs_receive.wait()
    success = success and btrfs_receive.returncode == 0
    middle.wait()
    success = success and middle.returncode == 0
    if middle is not btrfs_send:
        btrfs_send.wait()
        success = success and btrfs_send.returncode == 0
    if not success:
        raise RuntimeError(f'Btrfs failed to backup changes from {parent_path} '
                           f'to {snap_path} to {backup_snaps}')


def first_backup(
        todays_snaps: Path,
        backup_snaps: Path
) -> None:
    pv_exists = True
    try:
        subprocess.check_call(['pv', '--help'], stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pv_exists = False
    full_backup(pv_exists, todays_snaps.iterdir(), backup_snaps)


def incremental_backup(
        todays_snaps: Path,
        backup_snaps: Path,
        snapshot_library: Path,
        backup_library: Path,
        common_timestamp: dt.datetime
) -> None:
    pv_exists = True
    try:
        subprocess.check_call(['pv', '--help'], stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pv_exists = False
    backup_prev_snaps = backup_library / timestamp_as_string(common_timestamp)
    prev_snaps = snapshot_library / timestamp_as_string(common_timestamp)
    assert backup_prev_snaps.exists()
    previous_volumes = set()
    for p in backup_prev_snaps.iterdir():
        previous_volumes.add(p.name)
        if not (prev_snaps / p.name).exists():
            raise RuntimeError(
                f"{prev_snaps / p.name} does not exist and {p} does exist."
            )
    all_names = set(p.name for p in todays_snaps.iterdir())
    inc_names = all_names & previous_volumes
    full_names = all_names - previous_volumes
    full_backup(
        pv_exists,
        ((todays_snaps / name) for name in full_names),
        backup_snaps
    )
    for snap_path in (todays_snaps / name for name in inc_names):
        single_incremental_backup(
            pv_exists, snap_path, prev_snaps / snap_path.name, backup_snaps
        )

    def delete_snapshot_directory(snapshot_path: Path):
        assert snapshot_path.exists() and snapshot_path.is_dir()
        snaplist = [str(snapshot) for snapshot in snapshot_path.iterdir()]
        run_btrfs(['subvol', 'delete', '-c'] + snaplist)
        os.rmdir(snapshot_path)

    delete_snapshot_directory(prev_snaps)
    delete_snapshot_directory(backup_prev_snaps)


def timestamp_as_string(timestamp: dt.datetime) -> str:
    return dt.datetime.strftime(timestamp, timestamp_format)


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
    try:
        source_timestamps = inventory_library(snapshot_library)
    except ValueError as err:
        raise click.BadParameter(f"{snapshot_library} doesn't appear to be a "
                                 f"valid source snapshot library because "
                                 f"{err.args}")
    try:
        dest_timestamps = inventory_library(backup_library)
    except ValueError as err:
        raise click.BadParameter(f"{backup_library} doesn't appear to be a "
                                 f"valid destination snapshot library because "
                                 f"{err.args}")
    current_timestamp = dt.datetime.now()
    # Floor to the minute (as opposed to round, or ceil).
    current_timestamp -= dt.timedelta(
        seconds=current_timestamp.second,
        microseconds=current_timestamp.microsecond
    )
    if current_timestamp in (source_timestamps | dest_timestamps):
        raise RuntimeError("Backup already at least partly exists for "
                           f"{current_timestamp}")
    common_timestamps = list(
        sorted(set(source_timestamps.keys()) & set(dest_timestamps.keys()))
    )
    current_timestamp_str = timestamp_as_string(current_timestamp)
    # Create the directory to store the 'local' snapshot of the subvolumes to
    # backed up and the directory that contains the actual backup.
    todays_snaps = snapshot_library / current_timestamp_str
    try:
        todays_snaps.mkdir(exist_ok=False, parents=False)
    except FileExistsError:
        raise RuntimeError(f'Directory {todays_snaps} already exists')
    backup_snaps = backup_library / current_timestamp_str
    try:
        backup_snaps.mkdir(exist_ok=False, parents=False)
    except FileExistsError:
        raise RuntimeError(f'Backup {backup_snaps} already exists')
    for backup_link in backup_linkdir.iterdir():
        run_btrfs([
            'subvol', 'snap', '-r',
            str(backup_link),
            str(todays_snaps / backup_link.name)
        ])
    if len(common_timestamps) == 0:
        print("Doing an initial full backup.")
        first_backup(todays_snaps, backup_snaps)
    else:
        print(f"Doing an incremental backup from "
              f"{common_timestamps[-1]} to now.")
        incremental_backup(
            todays_snaps, backup_snaps,
            snapshot_library, backup_library, common_timestamps[-1]
        )


if __name__ == "__main__":
    main()
