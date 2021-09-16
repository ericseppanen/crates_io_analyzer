#!/usr/bin/python3 -u

from collections import defaultdict
import csv
import io
import json
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


def extract_crate_meta(data):
    """ Extract the cargo_vcs_info data from the crates.io tarball. """
    filelike = io.BytesIO(data)
    tarball = tarfile.open(fileobj=filelike, mode='r:gz')
    for member in tarball.getmembers():
        if member.name.endswith('.cargo_vcs_info.json'):
            json_data = tarball.extractfile(member).read()
            return json.loads(json_data)
    print('ERROR: no .cargo_vcs_info file in crate tarball')


# This extracts a crate/version listing from the crates.io database dump.
class CratesDbDump:
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


def git_clone_rev_read_tags(url, hash):
    """ Shallow-clone a repo, then find the tags associated with a hash.

    Returns (tags-list, errors)
    """
    repo_dir = tempfile.TemporaryDirectory()

    def run(*args, **kwargs):
        return subprocess.run(*args, **kwargs, cwd=repo_dir.name, check=True)

    run(['git', 'init', '-q', '.'])
    try:
        run(['git', 'remote', 'add', 'origin', url])
        run(['git', 'fetch', '-q', '-t', '--depth=1', 'origin', hash])
        result = run(['git', 'tag', '--points-at', hash], capture_output=True)
    except Exception as e:
        raise Exception(f'git error: {e}')
    tags = result.stdout.split()
    # Parse bytes to str.
    tags = [t.decode() for t in tags]
    return tags


def match_tags(crate, version, tags):
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
db = CratesDbDump('db-dump.tar.gz')

# The set of crates to investigate
TOP_START = 0
TOP_END = 100

for index, crate in enumerate(db.crates[TOP_START:TOP_END], start=TOP_START):
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
        tarball = download_crate(crate_name, latest)
        meta = extract_crate_meta(tarball)
    except:
        print(f'{crate_name} {latest} ERROR: failed to download crate')
    try:
        hash = meta['git']['sha1']
        print(f'{crate_name} {latest} scm hash {hash}')
        tags = git_clone_rev_read_tags(url, hash)
        match_tags(crate_name, latest, tags)
    except Exception as e:
        print(
            f'{crate_name} {latest} ERROR: failed reading tags for {crate_name} {latest}: {e}')
    time.sleep(2)
