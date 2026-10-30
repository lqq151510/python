"""Exercise backup/restore guards without contacting Docker or user data.

Each test copies the scripts into a private temporary root. A strict fake
`docker` accepts only the exact commands under test; it never spawns Docker.
"""

import concurrent.futures
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_STAMP = "20261030T120000Z"

FAKE_DOCKER = r"""#!/bin/bash
set -eu
if [[ "$(pwd -P)" != "$FAKE_ROOT" ]]; then exit 97; fi
case "$*" in
  "compose --project-name devassist exec -T db pg_dump --format=custom --username=postgres --dbname=app") event=dump ;;
  "compose --project-name devassist exec -T db pg_restore --list") event=list ;;
  "compose --project-name devassist exec -T db pg_restore --exit-on-error --file=/dev/null") event=verify ;;
  "compose --project-name devassist stop backend") event=stop ;;
  "compose --project-name devassist exec -T db dropdb --force --username=postgres app") event=drop ;;
  "compose --project-name devassist exec -T db createdb --username=postgres app") event=create ;;
  "compose --project-name devassist exec -T db pg_restore --exit-on-error --no-owner --username=postgres --dbname=app") event=restore ;;
  "compose --project-name devassist start backend") event=start ;;
  *) printf 'unrecognized\n' >> "$FAKE_LOG"; exit 88 ;;
esac
printf '%s\n' "$event" >> "$FAKE_LOG"
case "$event" in
  list|verify|restore)
    body="$(/bin/cat)"
    printf '%s:%s\n' "$event" "$body" >> "$FAKE_INPUT"
    ;;
esac
if [[ "${FAKE_MODE:-}" == "fail_$event" ]]; then exit 19; fi
if [[ "${FAKE_MODE:-}" == fail_source_verify && "$event" == verify && "$body" == CORRUPT-SOURCE ]]; then exit 19; fi
if [[ "${FAKE_MODE:-}" == fail_generated_verify && "$event" == verify && "$body" == SYNTHETIC-DUMP ]]; then exit 19; fi
if [[ "$event" == verify && "$body" == PRESERVE-ME ]]; then
  if [[ "${FAKE_MODE:-}" == mutate_inplace ]]; then printf 'TAMPERED' > "$FAKE_SOURCE"; fi
  if [[ "${FAKE_MODE:-}" == mutate_symlink ]]; then
    /bin/rm -f "$FAKE_SOURCE"
    /bin/ln -s "$FAKE_EVIL" "$FAKE_SOURCE"
  fi
fi
if [[ "$event" == dump ]]; then printf 'SYNTHETIC-DUMP'; fi
"""


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devassist-backup-mock-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("backup-devassist-db.sh", "restore-devassist-db.sh"):
            shutil.copy2(REPO_ROOT / "scripts" / name, self.scripts / name)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        self._executable(fake_bin / "docker", FAKE_DOCKER)
        self._executable(fake_bin / "date", f'#!/bin/bash\nprintf "{FIXED_STAMP}\\n"\n')
        # Use the current Python even when no system Python is on PATH.
        (fake_bin / "python3").symlink_to(Path(sys.executable).resolve())
        self.backups = self.root / ".local" / "devassist-backups"
        self.backups.mkdir(parents=True)
        self.log = self.root / "mock.log"
        self.inputs = self.root / "input.log"
        self.env = {
            "PATH": str(fake_bin) + os.pathsep + os.defpath,
            "FAKE_ROOT": str(self.root),
            "FAKE_LOG": str(self.log),
            "FAKE_INPUT": str(self.inputs),
        }
        self.original = self.backups / "preexisting.dump"
        self.original.write_text("PRESERVE-ME")

    @staticmethod
    def _executable(path: Path, content: str):
        path.write_text(content)
        path.chmod(0o700)

    def run_script(self, name: str, *args: Path, mode: str = "", confirm: bool = False):
        env = dict(self.env)
        if mode:
            env["FAKE_MODE"] = mode
        if confirm:
            env["DEVASSIST_RESTORE_CONFIRM"] = "RESTORE_DEVASSIST_DATABASE"
        return subprocess.run(
            ["/bin/bash", str(self.scripts / name), *(str(arg) for arg in args)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def events(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def assert_no_temporary_backups(self):
        self.assertEqual(list(self.backups.glob(".devassist-postgres-*")), [])

    def test_backup_success_checks_full_archive_before_publication(self):
        result = self.run_script("backup-devassist-db.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), ["dump", "list", "verify"])
        self.assertEqual(self.original.read_text(), "PRESERVE-ME")
        generated = list(self.backups.glob("devassist-postgres-*.dump"))
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].read_text(), "SYNTHETIC-DUMP")
        self.assert_no_temporary_backups()

    def test_backup_stage_failures_preserve_older_files(self):
        for mode in ("fail_dump", "fail_list", "fail_verify"):
            with self.subTest(mode=mode):
                result = self.run_script("backup-devassist-db.sh", mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.original.read_text(), "PRESERVE-ME")
                self.assertEqual(
                    list(self.backups.glob("devassist-postgres-*.dump")), []
                )
                self.assert_no_temporary_backups()

    def test_same_second_existing_backup_is_preserved(self):
        existing = self.backups / f"devassist-postgres-{FIXED_STAMP}.dump"
        existing.write_text("EXISTING-SAME-SECOND")
        result = self.run_script("backup-devassist-db.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(existing.read_text(), "EXISTING-SAME-SECOND")
        self.assertEqual(len(list(self.backups.glob("devassist-postgres-*.dump"))), 2)

    def test_simultaneous_backups_have_distinct_names(self):
        sentinel = self.backups / f"devassist-postgres-{FIXED_STAMP}.dump"
        sentinel.write_text("EXISTING-SENTINEL")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(lambda _: self.run_script("backup-devassist-db.sh"), range(6))
            )
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(sentinel.read_text(), "EXISTING-SENTINEL")
        generated = list(self.backups.glob("devassist-postgres-*.dump"))
        self.assertEqual(len(generated), 7)
        self.assertEqual(
            sum(path.read_text() == "SYNTHETIC-DUMP" for path in generated), 6
        )
        self.assert_no_temporary_backups()

    def test_publish_collisions_do_not_replace_file_directory_or_symlink(self):
        fake_mktemp = self.root / "bin" / "mktemp"
        self._executable(
            fake_mktemp,
            '#!/bin/bash\np="$FAKE_BACKUP_DIR/.devassist-postgres-20261030T120000Z.collision"\n'
            ': > "$p"\nprintf "%s\\n" "$p"\n',
        )
        self.env["FAKE_BACKUP_DIR"] = str(self.backups)
        output = self.backups / f"devassist-postgres-{FIXED_STAMP}.collision.dump"
        for kind in ("file", "directory", "symlink"):
            with self.subTest(kind=kind):
                external = self.root / "external-sentinel"
                external.write_text("EXTERNAL")
                if kind == "file":
                    output.write_text("KEEP-OLD")
                elif kind == "directory":
                    output.mkdir()
                else:
                    output.symlink_to(external)
                result = self.run_script("backup-devassist-db.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(output.exists())
                self.assertEqual(external.read_text(), "EXTERNAL")
                if kind == "file":
                    self.assertEqual(output.read_text(), "KEEP-OLD")
                if kind == "symlink":
                    self.assertTrue(output.is_symlink())
                if output.is_dir():
                    output.rmdir()
                else:
                    output.unlink()
                self.assert_no_temporary_backups()

    def test_restore_rejects_missing_or_extra_arguments(self):
        for args in ((), (self.original, self.original)):
            with self.subTest(argument_count=len(args)):
                result = self.run_script("restore-devassist-db.sh", *args, confirm=True)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(self.events(), [])

    def test_restore_rejects_empty_or_missing_source(self):
        empty = self.backups / "empty.dump"
        empty.touch()
        for source in (empty, self.backups / "missing.dump"):
            with self.subTest(source=source.name):
                result = self.run_script(
                    "restore-devassist-db.sh", source, confirm=True
                )
                self.assertEqual(result.returncode, 66)
                self.assertEqual(self.events(), [])

    def test_restore_requires_explicit_confirmation(self):
        result = self.run_script("restore-devassist-db.sh", self.original)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])

    def test_restore_rejects_outside_path_and_final_symlink(self):
        outside = self.root / "outside.dump"
        outside.write_text("NOT-TOUCH")
        link = self.backups / "linked.dump"
        link.symlink_to(outside)
        for path in (outside, link):
            with self.subTest(path=path.name):
                result = self.run_script("restore-devassist-db.sh", path, confirm=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.events(), [])
                self.assertEqual(outside.read_text(), "NOT-TOUCH")

    def test_restore_rejects_parent_symlink_escape(self):
        external = self.root / "external"
        external.mkdir()
        source = external / "outside.dump"
        source.write_text("EXTERNAL")
        (self.backups / "subdir").symlink_to(external, target_is_directory=True)
        result = self.run_script(
            "restore-devassist-db.sh",
            self.backups / "subdir" / source.name,
            confirm=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])
        self.assertEqual(source.read_text(), "EXTERNAL")

    def test_list_failure_happens_before_database_mutation(self):
        result = self.run_script(
            "restore-devassist-db.sh", self.original, mode="fail_list", confirm=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), ["list"])
        self.assertEqual(self.original.read_text(), "PRESERVE-ME")

    def test_bad_data_fails_full_precheck_before_database_mutation(self):
        source = self.backups / "bad.dump"
        source.write_text("CORRUPT-SOURCE")
        result = self.run_script(
            "restore-devassist-db.sh", source, mode="fail_source_verify", confirm=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), ["list", "verify"])
        self.assertEqual(source.read_text(), "CORRUPT-SOURCE")
        self.assertEqual(self.original.read_text(), "PRESERVE-ME")

    def test_prebackup_failure_never_stops_backend_or_drops_database(self):
        result = self.run_script(
            "restore-devassist-db.sh",
            self.original,
            mode="fail_generated_verify",
            confirm=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), ["list", "verify", "dump", "list", "verify"])
        self.assertEqual(self.original.read_text(), "PRESERVE-ME")

    def test_stop_failure_never_drops_database(self):
        result = self.run_script(
            "restore-devassist-db.sh", self.original, mode="fail_stop", confirm=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.events(), ["list", "verify", "dump", "list", "verify", "stop"]
        )

    def test_restore_stage_failure_never_starts_backend(self):
        for mode in ("fail_drop", "fail_create", "fail_restore"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                self.inputs.unlink(missing_ok=True)
                result = self.run_script(
                    "restore-devassist-db.sh", self.original, mode=mode, confirm=True
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("start", self.events())
                self.assertEqual(self.original.read_text(), "PRESERVE-ME")
                self.assertEqual(list(self.backups.glob(".devassist-restore.*")), [])

    def test_source_changes_after_precheck_do_not_change_restored_snapshot(self):
        self.env["FAKE_SOURCE"] = str(self.original)
        evil = self.root / "synthetic-evil.dump"
        evil.write_text("UNTRUSTED-REPLACEMENT")
        self.env["FAKE_EVIL"] = str(evil)
        protected = self.backups / "other-existing.dump"
        protected.write_text("PROTECT-OTHER-BACKUP")
        for mode in ("mutate_inplace", "mutate_symlink"):
            with self.subTest(mode=mode):
                self.original.unlink(missing_ok=True)
                self.original.write_text("PRESERVE-ME")
                self.log.unlink(missing_ok=True)
                self.inputs.unlink(missing_ok=True)
                result = self.run_script(
                    "restore-devassist-db.sh", self.original, mode=mode, confirm=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("restore:PRESERVE-ME", self.inputs.read_text())
                if mode == "mutate_inplace":
                    self.assertEqual(self.original.read_text(), "TAMPERED")
                else:
                    self.assertTrue(self.original.is_symlink())
                    self.assertEqual(self.original.read_text(), "UNTRUSTED-REPLACEMENT")
                self.assertEqual(protected.read_text(), "PROTECT-OTHER-BACKUP")
                self.assertEqual(evil.read_text(), "UNTRUSTED-REPLACEMENT")
                self.assertEqual(list(self.backups.glob(".devassist-restore.*")), [])

    def test_snapshot_copy_failure_and_symlink_race_never_touch_database(self):
        python = self.root / "bin" / "python3"
        python.unlink()
        real_python = shlex.quote(str(Path(sys.executable).resolve()))
        self._executable(
            python,
            "#!/bin/bash\n"
            'case "${3:-}" in\n'
            "  */.devassist-restore.*/source.dump)\n"
            '    if [[ "${FAKE_MODE:-}" == fail_copy ]]; then exit 23; fi\n'
            '    if [[ "${FAKE_MODE:-}" == race_symlink ]]; then\n'
            '      /bin/rm -f "$2"; /bin/ln -s "$FAKE_EVIL" "$2"\n'
            "    fi;;\n"
            "esac\n"
            f'exec {real_python} "$@"\n',
        )
        evil = self.root / "synthetic-evil.dump"
        evil.write_text("UNTRUSTED-REPLACEMENT")
        self.env["FAKE_EVIL"] = str(evil)
        for mode in ("fail_copy", "race_symlink"):
            with self.subTest(mode=mode):
                self.original.unlink(missing_ok=True)
                self.original.write_text("PRESERVE-ME")
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    "restore-devassist-db.sh", self.original, mode=mode, confirm=True
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.events(), [])
                if mode == "fail_copy":
                    self.assertEqual(result.returncode, 23)
                    self.assertEqual(self.original.read_text(), "PRESERVE-ME")
                else:
                    self.assertTrue(self.original.is_symlink())
                self.assertEqual(evil.read_text(), "UNTRUSTED-REPLACEMENT")
                self.assertEqual(list(self.backups.glob(".devassist-restore.*")), [])

    def test_restore_success_checks_and_preserves_source(self):
        result = self.run_script("restore-devassist-db.sh", self.original, confirm=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.events(),
            [
                "list",
                "verify",
                "dump",
                "list",
                "verify",
                "stop",
                "drop",
                "create",
                "restore",
                "start",
            ],
        )
        self.assertEqual(self.original.read_text(), "PRESERVE-ME")
        self.assertIn("restore:PRESERVE-ME", self.inputs.read_text())

    def test_restore_source_survives_same_second_prebackup(self):
        source = self.backups / f"devassist-postgres-{FIXED_STAMP}.dump"
        source.write_text("RESTORE-SOURCE-ORIGINAL")
        result = self.run_script("restore-devassist-db.sh", source, confirm=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.read_text(), "RESTORE-SOURCE-ORIGINAL")
        self.assertIn("restore:RESTORE-SOURCE-ORIGINAL", self.inputs.read_text())


if __name__ == "__main__":
    unittest.main()
