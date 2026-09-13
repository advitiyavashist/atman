# Community

Public testers and contributors submit changes as pull requests against
`origin/main`. `main` is protected: no force-push, no branch delete. Open a PR;
do not push straight to `main`.

This is not a CLA service. Contributions are under the existing
[MIT License](../LICENSE). Do not rewrite those terms.

## Fork, branch, PR

1. Fork [advitiyavashist/atman](https://github.com/advitiyavashist/atman) (or
   clone if you already have write access).
2. Branch from current `main`. One focused change set.

   ```sh
   git fetch origin
   git checkout -b your-name/short-topic origin/main
   ```

3. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) for setup and tests. Keep
   secrets, live board state, and machine paths out of the tree.
4. Push the branch and open a pull request into `main`. GitHub fills
   [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md):
   what changed, tests you ran, MIT grant checkbox, no secrets / live board.
5. Address review comments on the same branch. Maintainers merge.

## Review bar

A PR is ready when:

- The template checkboxes are honest (tests named, MIT grant, no secrets or
  live-board dumps).
- Behavior matches [CONTRIBUTING.md](../CONTRIBUTING.md): frozen contracts stay
  frozen unless the same change updates fixtures; no extra copies of domain
  logic; no claims from mocked benches.
- Docs that a public contributor will follow use relative links and do not
  assume the maintainers' ticket board.

Maintainers may request a narrower test command when the change is isolated.
There is no required CI status on `main` yet; a green local run of the
relevant lane is the bar unless a later ticket adds required checks.

## Conduct

[Code of Conduct](../CODE_OF_CONDUCT.md). Private reports go to the repository
owner via GitHub security advisory, not a public issue with extra personal data.

## License

[LICENSE](../LICENSE) (MIT). This README and this page are not a second grant.
