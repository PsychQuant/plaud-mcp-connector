"""Keep skill names consistent between the directory, the frontmatter, and the docs.

Renaming a skill touches four places that have no mechanical link to each other:
the directory under skills/, the `name:` field inside its SKILL.md, and every
mention in README.md and docs/. Miss one and nothing breaks loudly — the skill
still loads, the tests still pass, and the docs quietly point at a name that no
longer exists. A reader follows the doc, finds nothing, and concludes the feature
was never built.

That is exactly what happened when plaud-audit became plaud-repo-audit: the only
thing standing between a correct rename and three dangling references was a grep
somebody remembered to run. This file replaces the remembering.
"""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / "skills"

# Files that name skills in prose. A skill mentioned here must exist.
DOC_FILES = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]

# `plaud-<word>` in backticks — how every doc here refers to a skill. Bare
# mentions are deliberately not matched: "the plaud-grep path" in a sentence is
# prose, while `plaud-grep` is a claim that the thing exists.
SKILL_MENTION = re.compile(r"`(plaud-[a-z0-9-]+)`")

# Skills that ship inside Plaud's own npm package, not this repo.
# docs/official-surface.md documents them on purpose, so a mention is not a
# dangling reference — but the set is a MEASURED fact (surface audit, CLI/MCP
# 0.3.7, 2026-08-07), not a guess, and it is duplicated here deliberately.
#
# If Plaud ships an eighth skill and someone documents it, this test fails. That
# is the intended behaviour: a new official skill is an event worth noticing, and
# the failure routes it through `plaud-repo-audit` instead of letting it slide
# into the docs as though it had always been there.
OFFICIAL_SKILLS = {
    "plaud-shared",
    "plaud-find",
    "plaud-browse",
    "plaud-read",
    "plaud-followup",
    "plaud-digest",
    "plaud-export",
}


def skill_dirs():
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))


def frontmatter_name(skill_md: pathlib.Path) -> str | None:
    """The `name:` value from the YAML frontmatter, or None if absent.

    Deliberately stops at the closing `---`: `name:` also appears in prose and
    in the description block further down, and matching those would report a
    mismatch that is not one.
    """
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    front = text[3:end] if end != -1 else text
    match = re.search(r"^name:\s*(\S+)\s*$", front, re.MULTILINE)
    return match.group(1) if match else None


class TestSkillNames(unittest.TestCase):
    def test_every_skill_dir_has_a_skill_md(self):
        for skill in skill_dirs():
            self.assertTrue(
                (skill / "SKILL.md").is_file(),
                f"skills/{skill.name}/ has no SKILL.md — it will not load",
            )

    def test_frontmatter_name_matches_directory(self):
        """The directory name is what Claude Code shows; `name:` is what the skill calls
        itself. When they disagree, the user is told one name and the docs use the other."""
        for skill in skill_dirs():
            if not (skill / "SKILL.md").is_file():
                continue  # reported by test_every_skill_dir_has_a_skill_md
            declared = frontmatter_name(skill / "SKILL.md")
            self.assertIsNotNone(
                declared, f"skills/{skill.name}/SKILL.md has no `name:` in its frontmatter"
            )
            self.assertEqual(
                declared,
                skill.name,
                f"skills/{skill.name}/SKILL.md declares name: {declared} — "
                f"a rename updated the directory but not the frontmatter",
            )

    def test_docs_only_reference_known_skills(self):
        """A doc naming a skill that was renamed away sends the reader to nothing.

        Every mention must resolve to one of two places: a directory under
        skills/ (ours), or the official set above (Plaud's, documented on
        purpose). Anything else is either a typo, a rename that missed a file,
        or an official skill nobody has audited yet.
        """
        existing = {s.name for s in skill_dirs()}
        unresolved = []
        for doc in DOC_FILES:
            if not doc.is_file():
                continue
            for mention in sorted(set(SKILL_MENTION.findall(doc.read_text(encoding="utf-8")))):
                if mention in existing or mention in OFFICIAL_SKILLS:
                    continue
                unresolved.append(f"{doc.relative_to(REPO)} references `{mention}`")
        self.assertEqual(
            [],
            unresolved,
            "docs name skills that are neither ours nor known-official:\n  "
            + "\n  ".join(unresolved)
            + f"\n\nours: {sorted(existing)}"
            + f"\nofficial (measured): {sorted(OFFICIAL_SKILLS)}"
            + "\n\nIf it is ours, the rename missed a file. If it is Plaud's, run"
            " plaud-repo-audit and add it to OFFICIAL_SKILLS with the version"
            " you measured — do not just append the name to make this pass.",
        )


if __name__ == "__main__":
    unittest.main()
