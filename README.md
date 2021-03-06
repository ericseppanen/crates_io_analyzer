This article was originally published at [Code and Bitters](https://codeandbitters.com/published-crate-analysis/). Please use that URL when sharing this article.

# Rust: Does the published crate match the upstream source?

This project began the way many of my long Rust articles do-- I got curious about something. I started to wonder a few weeks ago about the relationship between crates that I download from crates.io, and the crate's upstream repository. Here are some of the questions I wanted to answer:

- How do I tell which git commit matches the published crate?
- Is there any guarantee that the published crate source matches the git source?
- What kinds of best practices exist? Is there room for improvement?

<!-- more -->

Why do I care about these things? There are a few reasons, but mostly this is a discussion about security and trust. Supply-chain attacks are unfortunately a real problem, and a compromised developer computer or a stolen crates.io token could allow a malicious package to be published and incorporated into a lot of downstream programs.

How quickly a malicious package would be detected depends a lot on how obvious the code change is. Not many people are likely to notice if a file is changed only in the published crate-- as developers we tend to assume that the upstream repository is the code that was published.

I don't believe there are monsters lurking under every bed, but I appreciate when the bed design allows me to see under every bed before deciding to sleep in it.

There's another reason I care about the ease of matching a published crate to the corresponding source code in the repository. Sometimes if I am tracking down an obscure bug, I want to add some debugging patches to the library sources. I usually begin by cloning that library's repository, and checking out the commit that corresponds to the same version I was using before. Unfortunately, some repositories don't have git tags for every release. Without tags, it can be tricky to work out exactly which commit corresponds to that release.

### What to expect when you download a crate from crates.io

If you just want to download a crate's source code without adding it as a dependency in another Rust project, you can install [cargo-clone] or [cargo-download].

I didn't find the crates.io download URL advertised anywhere, but you can construct it yourself: the URL is simply

> `https://static.crates.io/crates/{name}/{name}-{version}.crate`

So if you want to download `semver 1.0.4`, the download url is `https://static.crates.io/crates/semver/semver-1.0.4.crate`. The resulting `.crate` file is a gzipped tarball containing the source for that crate, Cargo manifest, and tests. It may not contain everything in the original repo. If you look at `semver-1.0.4.crate` as downloaded from crates.io, it has the `src` and `test` directories but does not contain the `fuzz` directory. Sometimes the upstream repository will contain multiple rust crates; only one crate's source will appear in the `.crate` file.

The integrity of the download can be verified by checking it against the sha256 checksum that is stored in the crates.io index.

> The crates.io index is a [git repository](https://github.com/rust-lang/crates.io-index) that contains a crate listing and some useful metadata. It's used by Cargo to discover available crate versions. Because it's a git repository, it would be quite difficult for an attacker to modify the checksum of an existing crate, so the index provides some protection against supply-chain attacks. Since connections to crates.io are also secured by TLS, compromising the crate download process would be very difficult.
>
> Crates are not cryptographically signed, though there have been some discussions about doing this in the future.

If we examine the `.crate` file that we downloaded, we find something useful: most crates come with an extra file called `.cargo_vcs_info.json`. This file contains a git hash:

```text
$ cat semver-1.0.4/.cargo_vcs_info.json
{
  "git": {
    "sha1": "ea9ea80c023ba3913b9ab0af1d983f137b4110a5"
  }
}
```

If we look at the [upstream git repo](https://github.com/dtolnay/semver/tree/ea9ea80c023ba3913b9ab0af1d983f137b4110a5), we do see that the `1.0.4` tag does point to that exact git hash, and if we inspect the files inside we can see that they do match the files we received from `crates.io`. Hooray!

### Asking questions about the crate publishing process

We looked at the `semver` crate and found some useful properties:
- The crates.io download provides a git hash.
- The upstream git repository has a matching release tag.
- The files from those two sources are the same.

But is there anything that guarantees this? Does this approach work for every crate?

Unfortunately, the answer to both questions is no.

Many crates don't provide a git hash; the `.cargo_vcs_info.json` file is missing from the downloaded tarball. This probably happens because the crate publisher uses `cargo publish --allow-dirty`, which according to the [cargo documentation](https://doc.rust-lang.org/cargo/commands/cargo-package.html), will cause the `.cargo_vcs_info.json` file to be omitted.

Additionally, many projects don't tag releases in git. This can make life difficult if you want to do local experiments on a crate: There's no straightforward way to make a local clone that exactly matches the upstream release.

Another problem that can happen is when a published crate contains a git hash, but that hash doesn't exist in the upstream repository. This may happen for harmless reasons; for example if the maintainer publishes the crate, then rewrites git history to correct a typo in a commit message, and then pushes the result to their public repository. Again, this makes life hard for someone who wants to experiment with that release, and it's very hard to verify that the published crate matches the source code in git.

A related problem is when the `Cargo.toml` manifest does not contain a repository link, or that link is incorrect or non-public.

The most alarming situation is when a crate is published, but the source code contained within does not appear in the upstream repository. This could indicate that something harmful is going on, like a malicious maintainer or a stolen credential, but is probably just human error. Either way, it's pretty hard to know whether or not this crate release should be trusted.

### How widespread are these problems?

I would like to live in a world where most of these questions can be answered quickly and easily, but unfortunately that's not really the case. It takes a lot of work to answer these questions and verify that published crates match the upstream sources.

Reasonable people will probably disagree about what crate publishing best practices should be, but I would generally categorize published crates into three categories:

* Gold star: published crates contain the git hash, and that hash has a git release tag, and all the source files match.
* Needs improvement: the published crate is missing the hash, or the upstream repository doesn't have release tags.
* Looks sketchy: the repository link doesn't work, or the published crate contains files that don't appear in the upstream repo.

I wrote [a script][this-repo] to download all of the most popular crates, and report any problems found when trying to match git hashes, tags, and file contents.

I limited my investigation to the most popular 500 crates, ranked by number of downloads. Here's what I found:

Unfortunately, only 319 crates (64% of the top 500) would earn the "gold star" badge.

**53 crates** (11%) contain no `.cargo_vcs_info.json` file, so we cannot easily find the git commit corresponding to the published code.

**115 crates** (23%) don't have an obviously matching git tag (e.g. `1.0.0` or `foo-v1.0.0`). A few crates are unfairly lumped in here because they have an unusual tagging style, but most of these (89 crates, 18%) don't have any git tags on the published commit.

**11 crates** (2%) contain a git hash in `.cargo_vcs_info.json`, but that hash does not exist in the upstream repository.

**4 crates** (1%) don't have a working git repository link. Three of these crates (`fuchsia-zircon`, `fuchsia-zircon-sys`, and `fuchsia-cprng`) point to `fuchsia.googlesource.com`, and return "permission denied" errors to both a web browser and a git client. Another crate is missing the repository link in its `Cargo.toml` manifest-- I opened an issue upstream, but haven't heard back yet.

**1 crate** (the 394th most downloaded) is unique among the top 500 in providing what appears to be a mercurial repository. I didn't add Mercurial support to my script, so I didn't analyze it further.

**5 crates** (1%) contain files in the crates.io tarball that do not appear anywhere in the linked git repo:
- One crate contained a unit test file that did not appear in the upstream repo. I reported this upstream and it was fixed a few days later.
- Two popular crates' latest releases are dummy packages that only contain one source file that throws a deliberate compiler error. The dummy source file isn't committed into the upstream git repo.
- Another crate contains what looks like an auto-generated file that isn't committed to git.
- The last mismatch in the top 500 had a `Cargo.toml` file that accidentally linked to the wrong repo. It had already been fixed by the time I went to report it.

That's a high number of issues, considering these are the 500 most-downloaded crates, that are published by some of the most experienced Rust developers in the world.

I haven't done a detailed analysis of the other 66,727 crates published on crates.io, but it's probably fair to assume things get less tidy as we go further down the list.

### Thoughts and further discussion

I really hope this doesn't come across as an attack on crates.io or Rust. I think the Rust ecosystem is amazing, and I have a huge amount of respect for the Rust developers and the crates.io team, and I can only express my thanks for building an amazing set of tools, and fostering an amazing community.

But it makes me sad that it's this hard to validate the provenance of source files in published crates. I wish that there was a robust set of best practices around crate publishing, and there was some way to nudge developers to follow those best practices.

I expect that these sort of questions will be asked within large companies' security teams, when they consider how they will adopt Rust, Cargo, and crates.io.

I would like to start a conversation with the crates.io team, to better understand the situation and find out if there's anything that could be done to improve the state of things. As a starting point I would love to see crates.io raise the visibility of some publishing quality issues:

- There should be a trusted source of crate publishing best practices. For example, it may be good to recommend `cargo-release`, which automates some of these steps, over `cargo-publish`, which allows for a lot of human error.
- Crates that follow best practices should be rewarded, with some kind of visible badge or improved search ranking. Alternatively, crates that don't follow best practices should be discouraged (with a danger badge or lowered search ranking).
- Publishing crates without any repository metadata should be discouraged. `cargo publish --allow-dirty` should be strongly discouraged as well.
- The git hash should be published on the crate release page on crates.io.

I could also imagine some more ambitious ideas:

- We could try to replace the error-prone local crate publishing procedure with a system where the developer pushes their source code to a public git repo first, and then asks crates.io to publish directly from the git repo. This would avoid many of the most common errors, and might make everything more friendly to CI automation.
- Crates.io could run its own automated verification of all crates, as a way of flagging suspect crates.
- If crate provenance/verification is widely accepted as a good idea, and crates.io were willing to publish some additional metadata, the Cargo could allow a local configuration setting that only permits "gold star" crates.

### Related discussions

Here I've collected a few links to related ideas/discussions.

Some very similar ideas were discussed in 2018 [here](https://internals.rust-lang.org/t/making-crates-io-verify-code-against-repository/14075).

The Cargo Registry structure is explained in [the Rust Book](https://doc.rust-lang.org/cargo/reference/registries.html). The [crates_index](https://docs.rs/crates-index/) crate provides access to the crates.io index repository that contains crate version information (and sha256 checksums).

Cargo issue [#1281](https://github.com/rust-lang/cargo/issues/1281) and crates.io issue [#75](https://github.com/rust-lang/crates.io/issues/75) have some discussion of "crate signing" ideas.

crates.io issues [#1592](https://github.com/rust-lang/crates.io/issues/1592) and [#2113](https://github.com/rust-lang/crates.io/issues/2113) describe some of the reasons for crate download URLs not being advertised.

crates.io issue [#1462](https://github.com/rust-lang/crates.io/issues/1462) proposed a similar "publish from git" model.

[cargo-crev](https://github.com/crev-dev/cargo-crev) demonstrates some interesting ideas for managing trust in dependencies.

### My crate analysis procedure

Feel free to skip this part: it contains some additional details of how I scripted the analysis of the top 500 crates.

This process is not the most elegant thing in the world, and the source code is about what you'd expect from a weekend project that grew out of control. Feel free to [suggest improvements][this-repo]!

The `crate_inspector.py` script builds its list of crates from the published [crates.io database dump](https://crates.io/data-access). I used a dump from 2021-09-09. I only analyzed the latest version of each crate.

For each crate analyzed, the script does the following:
- Attempt to download the crate. This should always succeed (because it's in the database dump), but there are a few crates that return `403 Forbidden`. I don't know why (presumably security concerns or legal issues?) Since none of these crates appear in the top 500 list, I didn't need to mention them above.
- Extract the `.cargo_vcs_info.json` file, if it exists, and grab the git hash.
- Extract the `repository` url from `Cargo.toml`. Attempt to massage that URL into a form that's compatible with the standard git client (many projects use a github branch URL that only works in a web browser).
- If we have a git hash from crates.io, try to do a shallow clone of only that commit; then check to see if that commit has a matching release tag.
- Otherwise, try to do a full clone of the upstream repo.
- Walk the crates.io tarball; generate a git blob hash for each `*.rs` file and query whether git can find that blob in the local repo. This is a brute-force strategy to avoid trying to find and match the exact tree structure in git.
- If we did a shallow clone and failed to find a blob, do a full clone and repeat the search.

From that point on, I did my analysis by hand, reading the script output and investigating each category of error to try to understand why it occurs.

If you'd like to see the raw script output without having to run it yourself, you can view my "top 500" analysis log file [here](https://raw.githubusercontent.com/ericseppanen/crates_io_analyzer/main/results/2021_09_09/top500.txt).

Thanks for reading!

---

Comments? 

Please get in touch on [twitter: @codeandbitters](https://twitter.com/codeandbitters)

You can find my crate analysis script [here][this-repo].

[cargo-clone]: https://github.com/JanLikar/cargo-clone
[cargo-download]: https://github.com/Xion/cargo-download
[this-repo]: https://github.com/ericseppanen/crates_io_analyzer
