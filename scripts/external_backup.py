"""Create a verified private backup and publish it to an explicitly configured SSH host."""
import argparse
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import production as p


def archive_backup(folder, token):
    folder = Path(folder)
    if folder.is_symlink(): raise RuntimeError('Backup directory may not be a symlink')
    manifest = p.verify_backup(folder, token)
    names = sorted([*manifest['files'], 'manifest.json', 'COMPLETE'])
    if not re.fullmatch(r'[A-Za-z0-9._-]+', folder.name): raise ValueError('Invalid backup name')
    archive = folder.with_name(folder.name+'.tar.gz')
    temporary = archive.with_name(archive.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temporary, 'xb', opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            with tarfile.open(fileobj=output, mode='w:gz') as bundle:
                for name in names:
                    path = folder/name
                    if path.is_symlink() or not path.is_file(): raise RuntimeError('Invalid backup member: '+name)
                    info = bundle.gettarinfo(str(path), arcname=name)
                    info.mode = 0o600; info.uid = info.gid = 0; info.uname = info.gname = ''
                    with path.open('rb') as source: bundle.addfile(info, source)
            output.flush(); os.fsync(output.fileno())
        # Reject changed source files before replacing a previously valid archive.
        p.verify_backup(folder, token)
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def upload_verified(archive, host, remote_directory, *, runner=p.run):
    if not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*', host or ''):
        raise ValueError('Use a DNS host with an optional SSH user')
    if not re.fullmatch(r'/[A-Za-z0-9._/-]+', remote_directory or '') or '..' in remote_directory.split('/'):
        raise ValueError('Use an absolute remote directory without shell syntax or parent traversal')
    archive = Path(archive).resolve()
    if not archive.is_file() or not re.fullmatch(r'[A-Za-z0-9._-]+', archive.name):
        raise ValueError('A completed local archive is required')
    directory = remote_directory.rstrip('/')
    destination = directory+'/'+archive.name
    partial = destination+'.'+uuid.uuid4().hex+'.part'
    options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
    ssh = ['ssh', *options, host]
    runner([*ssh, 'umask 077; mkdir -p -- '+shlex.quote(directory)], what='Prepare external backup directory')
    runner(['scp', *options, str(archive), host+':'+partial], what='Upload private external backup')
    digest = runner([*ssh, 'sha256sum -- '+shlex.quote(partial)], what='Verify external backup checksum')
    if not digest or digest.split()[0] != p.sha256(archive):
        raise RuntimeError('External backup checksum mismatch; complete local backup preserved')
    runner([*ssh, 'chmod 600 -- '+shlex.quote(partial)+' && mv -- '+shlex.quote(partial)+' '+shlex.quote(destination)],
           what='Publish verified external backup')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=str(ROOT/'.env.production'))
    parser.add_argument('--compose-file', default=str(ROOT/'compose.production.yaml'))
    parser.add_argument('--project')
    parser.add_argument('--host', default=os.getenv('BACKUP_SSH_HOST'))
    parser.add_argument('--remote-directory', default=os.getenv('BACKUP_REMOTE_DIRECTORY'))
    args = parser.parse_args()
    if not args.host or not args.remote_directory:
        parser.error('Set BACKUP_SSH_HOST and BACKUP_REMOTE_DIRECTORY, or pass both options')
    os.umask(0o077)
    stack = p.Stack(args.env_file, args.compose_file, args.project)
    with p.operation_lock(stack):
        folder = p.backup(stack)
        archive = archive_backup(folder, stack.token())
        destination = upload_verified(archive, args.host, args.remote_directory)
        p.private_json(folder/'external-copy.json', {'host': args.host, 'path': destination,
            'sha256': p.sha256(archive), 'verified_at': p.stamp()})
    print('Verified external backup:', destination)


if __name__ == '__main__': main()
