import unittest
from unittest.mock import MagicMock, patch

from app.docker_compose_apply import (
    SettingsInfraChanges,
    apply_settings_changes,
    reload_caddy_config,
    validate_caddy_config,
)


class SettingsInfraChangesTest(unittest.TestCase):
    def test_caddy_config_when_public_url_changes(self) -> None:
        changes = SettingsInfraChanges(public_url=True)
        self.assertTrue(changes.caddy_config)
        self.assertFalse(changes.docker_dns)
        self.assertTrue(changes.any_change)

    def test_no_change_skips_apply(self) -> None:
        result = apply_settings_changes(SettingsInfraChanges())
        self.assertEqual(result["dns_apply_message"], "Impostazioni salvate.")
        self.assertFalse(result["caddy_reloaded"])
        self.assertFalse(result["dns_applied"])


class CaddyReloadTest(unittest.TestCase):
    @patch("app.docker_compose_apply._docker_exec")
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_validate_returns_error_detail(self, _available, docker_exec) -> None:
        docker_exec.return_value = MagicMock(returncode=1, stdout="", stderr="bad host")
        ok, detail = validate_caddy_config()
        self.assertFalse(ok)
        self.assertIn("bad host", detail)

    @patch("app.docker_compose_apply._docker_exec")
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_reload_validates_before_reload(self, _available, docker_exec) -> None:
        docker_exec.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="invalid config"),
        ]
        ok, detail = reload_caddy_config()
        self.assertFalse(ok)
        self.assertIn("invalid config", detail)
        self.assertEqual(docker_exec.call_count, 1)

    @patch("app.docker_compose_apply._docker_exec")
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_reload_runs_validate_then_reload(self, _available, docker_exec) -> None:
        docker_exec.side_effect = [
            MagicMock(returncode=0, stdout="Valid configuration", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        ok, detail = reload_caddy_config()
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(docker_exec.call_count, 2)
        self.assertEqual(docker_exec.call_args_list[1].args[1][0], "caddy")
        self.assertEqual(docker_exec.call_args_list[1].args[1][1], "reload")


class ApplySettingsChangesTest(unittest.TestCase):
    @patch("app.docker_compose_apply.reload_caddy_config", return_value=(True, ""))
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_public_url_change_only_reloads_caddy(
        self, _available, reload_caddy
    ) -> None:
        result = apply_settings_changes(SettingsInfraChanges(public_url=True))
        reload_caddy.assert_called_once_with()
        self.assertTrue(result["caddy_reloaded"])
        self.assertFalse(result["dns_applied"])
        self.assertFalse(result["apply_failed"])

    @patch("app.docker_compose_apply.validate_caddy_config", return_value=(True, ""))
    @patch("app.docker_compose_apply._apply_docker_dns_settings")
    @patch("app.docker_compose_apply._compose_cmd", return_value=["docker", "compose"])
    @patch("app.docker_compose_apply._COMPOSE_FILE")
    @patch("app.docker_compose_apply._DNS_OVERRIDE")
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_dns_change_recreates_stack_not_caddy_reload(
        self,
        _available,
        dns_override,
        compose_file,
        _compose_cmd,
        apply_dns,
        _validate,
    ) -> None:
        compose_file.is_file.return_value = True
        dns_override.is_file.return_value = True
        apply_dns.return_value = (True, "DNS ok", True)

        result = apply_settings_changes(SettingsInfraChanges(docker_dns=True))

        apply_dns.assert_called_once()
        self.assertTrue(result["dns_applied"])
        self.assertFalse(result["caddy_reloaded"])
        self.assertFalse(result["apply_failed"])

    @patch("app.docker_compose_apply.reload_caddy_config", return_value=(False, "boom"))
    @patch("app.docker_compose_apply.docker_restart_available", return_value=True)
    def test_caddy_reload_failure_marks_apply_failed(
        self, _available, _reload
    ) -> None:
        result = apply_settings_changes(SettingsInfraChanges(public_url=True))
        self.assertTrue(result["apply_failed"])
        self.assertIn("boom", result["dns_apply_message"])
