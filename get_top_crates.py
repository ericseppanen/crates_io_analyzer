#!/usr/bin/python3

from collections import defaultdict
import csv
import io
import json
import semver
import tarfile
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


def load_crates():
    with open('2021-09-09-020123/data/crates.csv') as crates_file:
        data = csv.reader(crates_file)
        # crates.csv:
        #     0           1          2             3        4      5       6          7     8        9         10
        # created_at,description,documentation,downloads,homepage,id,max_upload_size,name,readme,repository,updated_at
        header = crates_csv_extract(data.__next__())
        assert header == ('downloads', 'id', 'name', 'repository')

        crates = []

        for row in data:
            crates.append(crates_csv_extract(row))

        crates.sort(reverse=True)
        return crates


def load_versions():
    with open('2021-09-09-020123/data/versions.csv') as versions_file:
        data = csv.reader(versions_file)
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

        return versions


crates = load_crates()
versions = load_versions()

TOPN = 10

for crate in crates[:TOPN]:
    crate_id = crate[1]
    crate_name = crate[2]
    vers = versions[crate_id]
    latest = latest_version(vers)
    print(f'{crate[0]} {crate_name} {latest} {crate[3]}')
    tarball = download_crate(crate_name, latest)
    print(f'got {len(tarball)} bytes')
    meta = extract_crate_meta(tarball)
    print('meta:', meta)
    break
