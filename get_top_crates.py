#!/usr/bin/python3 -u

import argparse
from collections import defaultdict
import csv
import hashlib
import io
import json
import re
import semver
import subprocess
import tarfile
import tempfile
import time
import urllib.request

csv.field_size_limit(1024 * 1024)


def try_int(x):
    try:
        return int(x)
    except:
        return x


def crates_csv_extract(row):
    """ return a tuple (downloads, id, name, repository) """
    return (try_int(row[3]), try_int(row[5]), row[7], row[9])


def versions_csv_extract(row):
    """ return a tuple (crate_id, version) """
    return (try_int(row[0]), row[7])


def try_semver(s):
    """ try to parse a semver string """
    try:
        return semver.VersionInfo.parse(s)
    except:
        return s


def latest_version(vlist):
    vlist = sorted(vlist, reverse=True)
    return vlist[0][1]


def download_crate(name, version):
    url = f'https://crates.io/api/v1/crates/{name}/{version}/download'
    response = urllib.request.urlopen(url)
    assert response.status == 200
    return response.read()


class CrateTarball:
    def __init__(self, data):
        filelike = io.BytesIO(data)
        self.tarball = tarfile.open(fileobj=filelike, mode='r:gz')

    def extract_crate_meta(self):
        """ Extract the cargo_vcs_info data from the crates.io tarball. """
        for member in self.tarball.getmembers():
            if member.name.endswith('.cargo_vcs_info.json'):
                json_data = self.tarball.extractfile(member).read()
                return json.loads(json_data)
        print('ERROR: no .cargo_vcs_info file in crate tarball')

    def examine_files(self, filename_pattern):
        """ Provide an iterator over files that match a particular pattern.

        The filename_pattern is in python regex format.

        Returns (filename, file_reader)

        """
        for member in self.tarball.getmembers():
            if re.match(filename_pattern, member.name):
                #print(f'*** examine file {member.name}')
                yield (member.name, self.tarball.extractfile(member))


class CratesDbDump:
    """ This extracts a crate/version listing from the crates.io database dump. """

    def __init__(self, filename):
        tarball = tarfile.open(filename)
        for member in tarball.getmembers():
            if member.name.endswith('crates.csv'):
                tar_reader = tarball.extractfile(member)
                text_reader = io.TextIOWrapper(tar_reader)
                self.load_crates(text_reader)
            elif member.name.endswith('versions.csv'):
                tar_reader = tarball.extractfile(member)
                text_reader = io.TextIOWrapper(tar_reader)
                self.load_versions(text_reader)
        assert self.crates
        assert self.versions

    def load_crates(self, reader):
        data = csv.reader(reader)
        # crates.csv:
        #     0           1          2             3        4      5       6          7     8        9         10
        # created_at,description,documentation,downloads,homepage,id,max_upload_size,name,readme,repository,updated_at
        header = crates_csv_extract(data.__next__())
        assert header == ('downloads', 'id', 'name', 'repository')

        crates = []

        for row in data:
            crates.append(crates_csv_extract(row))

        # Do a reverse-sort so that the most popular crates come first.
        crates.sort(reverse=True)
        self.crates = crates

    def load_versions(self, reader):
        data = csv.reader(reader)
        # versions.csv:
        #     0         1          2         3         4     5    6     7      8            9        10
        # crate_id,crate_size,created_at,downloads,features,id,license,num,published_by,updated_at,yanked
        header = versions_csv_extract(data.__next__())
        assert header == ('crate_id', 'num')

        versions = defaultdict(list)
        for row in data:
            crate_id, version_string = versions_csv_extract(row)
            vers = try_semver(version_string)
            versions[crate_id].append((vers, version_string))

        self.versions = versions


class Git:
    def __init__(self):
        self.repo_dir = tempfile.TemporaryDirectory()

    def run(self, *args, **kwargs):
        try:
            kwargs['cwd'] = self.repo_dir.name
            if 'check' not in kwargs:
                kwargs['check'] = True
            return subprocess.run(*args, **kwargs)
        except Exception as e:
            print(f'git error: {e}')
            raise

    @staticmethod
    def hash_blob(data):
        """ hash some bytes the same way git hashes a file. """

        # FIXME: might fail if the file is stored with different line-endings?
        stuff = b'blob ' + str(len(data)).encode() + b'\0'
        return hashlib.sha1(stuff + data).hexdigest()

    def blob_exists(self, blob_hash):
        """ Returns True if the blob exists in this repo. """

        result = self.run(['git', 'cat-file', '-e', blob_hash], check=False)
        return result.returncode == 0

    def clone_full(self, url):
        """ Clone a repo. """
        self.run(['git', 'clone', '-q', '--bare', url, '.'])

    def clone_rev_read_tags(self, url, hash):
        """ Shallow-clone a repo, then find the tags associated with a hash.

        Returns (tags-list, errors)
        """

        self.run(['git', 'init', '-q', '.'])
        self.run(['git', 'remote', 'add', 'origin', url])
        self.run(['git', 'fetch', '-q', '-t', '--depth=1', 'origin', hash])
        result = self.run(['git', 'tag', '--points-at',
                          hash], capture_output=True)

        tags = result.stdout.split()
        # Parse bytes to str.
        tags = [t.decode() for t in tags]
        return tags


def match_tags(crate, version, tags):
    """ Examine a list of tags to see if one matches this version.

    The tag formats we expect are:
    - 1.2.3
    - v1.2.3
    - cratename-1.2.3
    - cratename-v1.2.3

    """
    def try_match(tag):
        if tag in tags:
            print(f'{crate} {version} hash matches tag "{tag}"')
            return True
        return False

    success = try_match(version) or try_match(
        f'v{version}') or try_match(f'{crate}-{version}') or try_match(f'{crate}-v{version}')
    if not success:
        print(f'tag fail: {tags}')


# Download from https://static.crates.io/db-dump.tar.gz
DBDUMPFILE_DEFAULT = 'db-dump.tar.gz'


def parse_range(val):
    """ Parse a number or a numeric range, e.g. '7' or '0-10'. """
    vals = val.split(sep='-', maxsplit=1)
    if len(vals) == 1:
        x = int(vals[0])
        return (x, x + 1)
    if len(vals) == 2:
        return (int(vals[0]), int(vals[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dbdumpfile', default=DBDUMPFILE_DEFAULT)
    parser.add_argument('--rank', type=parse_range, default=(0, 100))
    args = parser.parse_args()

    db = CratesDbDump(args.dbdumpfile)

    # Examine crates by their ranking
    rank_start, rank_end = args.rank

    for index, crate in enumerate(db.crates[rank_start:rank_end], start=rank_start):
        print(f'ranking: {index}')
        crate_id = crate[1]
        crate_name = crate[2]
        vers = db.versions[crate_id]
        latest = latest_version(vers)
        url = crate[3]
        print(f'{crate_name} {latest} has {crate[0]} downloads')
        print(f'{crate_name} {latest} repo url is {url}')

        # Try to download the crate source from crates.io .
        # We don't write it to a file, but instead examine it in-memory.
        try:
            tarball_data = download_crate(crate_name, latest)
        except Exception as e:
            print(f'{crate_name} {latest} ERROR: failed to download crate: {e}')
            # If crates.io is unreachable, then we should just stop the script.
            raise
        try:
            tarball = CrateTarball(tarball_data)
        except Exception:
            print(f'{crate_name} {latest} ERROR: failed to extract crate tarball')
            continue

        def match_hash_exact():
            try:
                meta = tarball.extract_crate_meta()
                hash = meta['git']['sha1']
            except Exception:
                print(
                    f'{crate_name} {latest} ERROR: failed to extract crate metadata')
                raise
            try:
                print(f'{crate_name} {latest} scm hash {hash}')
                tags = Git().clone_rev_read_tags(url, hash)
                match_tags(crate_name, latest, tags)
            except Exception as e:
                print(
                    f'{crate_name} {latest} ERROR: failed reading tags for {crate_name} {latest}: {e}')
                raise

        # TODO: allow multiple strategies:
        # - lazy: just search for a matching tag at the specified scm hash
        # - fallback: try "lazy" but fall back to a full clone object search
        # - object-search: clone the full repo and try to find the matching files

        try:
            match_hash_exact()
        except Exception:
            print('exact scm match failed.')

        if True:
            print(f'{crate_name} {latest} doing full repo clone...')
            repo = Git()
            repo.clone_full(url)
            file_count = 0
            files_unmatched = 0
            for filename, file_reader in tarball.examine_files('.*\.rs$'):
                blob_hash = repo.hash_blob(file_reader.read())
                blob_exists = repo.blob_exists(blob_hash)
                file_count += 1
                if not blob_exists:
                    print(
                        f'{crate_name} {latest} ERROR: file not in repo: {filename}')

                    files_unmatched += 1
            print(
                f'{crate_name} {latest} files checked: {file_count} unmatched files: {files_unmatched}')

            # Next steps:
            # for each .rs file in the download, compute its git blob hash and check whether that
            # object exists in the repo.

            # TODO: if the crate download contains a meta hash, check whether the tarball files
            # match the files in that rev.

        time.sleep(2)


if __name__ == '__main__':
    main()
