# btrfs_snapback

A backup tool for systems using btrfs. This has a very rigid idea of how
backups should be performed.

## This does not yet work at all ##

This project is licensed under the GPLv3 or greater. A copy of the
license is in the file named LICENSE.

Assumptions:

 * You have a directory of relative symlinks to the btrfs subvolumes you
want to backup.
 * You also have a directory containing subdirectories of snapshots.
   * Each such subdirectory is named `YYYY-MM-DD-HH:MM` for when that
     set of snapshots was made.
 * You have a different btrfs filesystem that also has a directory set
   up exactly like the previously mentioned directory on the filesystem
   to be backed up.

When run, it will create a new subdirectory in your source for the
current date and time and create a set of snapshots from the directory
of symbolic links. It will then search for a common subdirectory on the
backup filesystem and if it can't find one, it will send a full snapshot
to the new filesystem. If it can find one, it sends an incremental
update from that timed snapshot to the current one instead.

Part of my motivation for this is a system for offsite backups involving
trading backup media with a remote location. This would mean a sort of
staggered set of backups using the two media.
