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
        except subprocess.CalledProcessError as e:
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

    def clone_shallow(self, url, commit_hash):
        """ Shallow-clone a repo, then find the tags associated with a hash. """

        self.run(['git', 'init', '-q', '.'])
        self.run(['git', 'remote', 'add', 'origin', url])
        self.run(['git', 'fetch', '-q', '-t',
                 '--depth=1', 'origin', commit_hash])

    def get_tags(self, commit_hash):
        """ Find the tags associated with a commit.

        Returns a list of strings (one for each tag).
        """

        result = self.run(['git', 'tag', '--points-at',
                          commit_hash], capture_output=True)

        tags = result.stdout.split()
        # Parse bytes to str.
        tags = [t.decode() for t in tags]
        return tags


class Verifier:
    """ Do all the verification steps on a crate. """

    def __init__(self, crate_name, crate_version, repo_url):
        self.repo = None
        self.commit_hash = None
        self.crate_name = crate_name
        self.crate_version = crate_version
        self.repo_url = repo_url

    def print(self, msg):
        print(f'{self.crate_name} {self.crate_version} {msg}')

    def download(self):
        # Try to download the crate source from crates.io .
        # We don't write it to a file, but instead examine it in-memory.
        try:
            tarball_data = download_crate(self.crate_name, self.crate_version)
        except Exception as e:
            self.print(f'ERROR: failed to download crate: {e}')
            # If crates.io is unreachable, then we should just stop the script.
            raise
        try:
            self.tarball = CrateTarball(tarball_data)
        except Exception:
            self.print(f'ERROR: failed to extract crate tarball')
            raise

    def match_tags(self):
        """ Examine a list of tags to see if one matches this version.

        The tag formats we expect are:
        - 1.2.3
        - v1.2.3
        - cratename-1.2.3
        - cratename-v1.2.3

        returns True if a tag match is found, False otherwise.

        """

        try:
            tags = self.repo.get_tags(self.commit_hash)
        except Exception as e:
            self.print(f'ERROR: failed to read tags: {e}')

        def try_match(tag):
            if tag in tags:
                self.print(f'commit-hash matches tag "{tag}"')
                return True
            return False

        nn = self.crate_name
        vv = self.crate_version
        success = try_match(self.crate_version) or try_match(
            f'v{vv}') or try_match(f'{nn}-{vv}') or try_match(f'{nn}-v{vv}')
        if not success:
            self.print(f'ERROR: tag match fail: {tags}')
        return success

    def clone_shallow(self):
        """ Try to extract the crates.io scm hash, and then shallow-clone the repo. """
        try:
            meta = self.tarball.extract_crate_meta()
            self.commit_hash = meta['git']['sha1']
        except Exception:
            self.print(f'ERROR: failed to extract crate metadata')
            raise
        self.print(f'scm hash {self.commit_hash}')

        try:
            repo = Git()
            repo.clone_shallow(self.repo_url, self.commit_hash)
            self.repo = repo
        except Exception as e:
            self.print(f'ERROR: failed to clone repo: {e}')
            raise

    def clone_full(self):
        try:
            self.print(f'doing full repo clone...')
            repo = Git()
            repo.clone_full(self.repo_url)
            self.repo = repo
        except Exception as e:
            self.print(f'ERROR: failed to clone repo: {e}')
            raise

    def search_blobs(self):
        """ Returns True if all files exist in the repo. """
        file_count = 0
        files_unmatched = 0
        for filename, file_reader in self.tarball.examine_files('.*\.rs$'):
            blob_hash = self.repo.hash_blob(file_reader.read())
            blob_exists = self.repo.blob_exists(blob_hash)
            file_count += 1
            if not blob_exists:
                self.print(f'ERROR: file not in repo: {filename}')
                files_unmatched += 1
        self.print(
            f'files checked: {file_count} unmatched files: {files_unmatched}')
        if files_unmatched:
            self.print(f'ERROR: {files_unmatched} files not found in repo')
        return files_unmatched == 0


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

        verifier = Verifier(crate_name, latest, url)

        verifier.print(f'has {crate[0]} downloads')
        verifier.print(f'repo url is {url}')

        # TODO: allow multiple strategies:
        # - lazy: just search for a matching tag at the specified scm hash
        # - fallback: try "lazy" but fall back to a full clone object search
        # - object-search: clone the full repo and try to find the matching files

        # TODO: if the crate download contains a meta hash, check whether the tarball files
        # match the files in that rev.

        verifier.download()

        try:
            verifier.clone_shallow()
            verifier.match_tags()
            if not verifier.search_blobs():
                # Hacky way of forcing the clone_full to execute.
                raise Exception
        except Exception:
            print('shallow match failed.')
            try:
                verifier.clone_full()
                verifier.search_blobs()
            except Exception:
                # Clone failed, nothing more we can do.
                continue

        time.sleep(2)


if __name__ == '__main__':
    main()
