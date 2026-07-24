import os
import socket
import tempfile
import unittest
import urllib.request
from unittest import mock

import network_utils


class ProxyDecisionTests(unittest.TestCase):
    def test_stale_environment_proxy_is_ignored_for_live_windows_proxy(self):
        environment = {
            "HTTP_PROXY": "http://127.0.0.1:52846",
            "HTTPS_PROXY": "http://127.0.0.1:52846",
            "ALL_PROXY": "http://127.0.0.1:52846",
        }

        def listener(proxy_url):
            return proxy_url.endswith(":10808")

        decision = network_utils.resolve_proxy(
            config={},
            environ=environment,
            windows_proxy="127.0.0.1:10808",
            listener_checker=listener,
        )

        self.assertEqual(decision.url, "http://127.0.0.1:10808")
        self.assertEqual(decision.source, "windows")
        self.assertTrue(any("52846" in item and "未监听" in item
                            for item in decision.warnings))

    def test_explicit_proxy_has_priority_over_system_and_environment(self):
        decision = network_utils.resolve_proxy(
            config={"proxy": "127.0.0.1:7890"},
            environ={"HTTPS_PROXY": "http://127.0.0.1:7892"},
            windows_proxy="127.0.0.1:7891",
            listener_checker=lambda value: True,
        )

        self.assertEqual(decision.url, "http://127.0.0.1:7890")
        self.assertEqual(decision.source, "config")
        self.assertTrue(any("代理配置冲突" in item for item in decision.warnings))

    def test_valid_environment_proxy_is_used_without_other_proxy(self):
        decision = network_utils.resolve_proxy(
            config={},
            environ={"HTTPS_PROXY": "http://127.0.0.1:7893"},
            windows_proxy="",
            listener_checker=lambda value: True,
        )

        self.assertEqual(decision.url, "http://127.0.0.1:7893")
        self.assertEqual(decision.source, "environment")

    def test_no_proxy_clears_only_current_process_environment(self):
        environment = {
            "HTTP_PROXY": "http://127.0.0.1:59999",
            "HTTPS_PROXY": "http://127.0.0.1:59999",
            "KEEP_ME": "yes",
        }
        report = network_utils.initialize_network_environment(
            config={},
            environ=environment,
            windows_proxy="",
            listener_checker=lambda value: False,
            process_names=[],
            check_dns=False,
        )

        self.assertIsNone(report.decision.url)
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertEqual(environment["KEEP_ME"], "yes")

    def test_multiple_proxy_families_raise_operator_warning(self):
        report = network_utils.initialize_network_environment(
            config={},
            environ={},
            windows_proxy="",
            process_names=[
                "v2rayN.exe", "xray.exe", "com.vortex.helper.exe", "Bitz Net.exe",
            ],
            check_dns=False,
        )

        warning = "\n".join(report.warnings)
        self.assertIn("多套代理程序", warning)
        self.assertIn("v2rayN/xray", warning)
        self.assertIn("Bitz Net 后台服务 (com.vortex.helper)", warning)
        self.assertEqual(warning.count("Bitz Net 后台服务"), 1)


class NetworkOpenerTests(unittest.TestCase):
    def test_direct_opener_installs_empty_proxy_handler(self):
        captured = {}

        def fake_build_opener(handler):
            captured["handler"] = handler

            class Fake:
                pass

            return Fake()

        with mock.patch.object(network_utils.request, "build_opener", fake_build_opener):
            opener = network_utils.build_network_opener("direct", config={})

        self.assertEqual(captured["handler"].proxies, {})
        self.assertEqual(opener.route, "direct")

    def test_connection_refused_error_identifies_proxy_stage(self):
        decision = network_utils.ProxyDecision(
            "http://127.0.0.1:10808",
            "windows",
        )
        exc = OSError(10061, "actively refused")
        exc.winerror = 10061

        message = network_utils.explain_network_error(
            exc,
            "api.example.com",
            "proxy",
            decision,
        )

        self.assertIn("代理连接失败", message)
        self.assertIn("Windows 系统代理", message)
        self.assertIn("127.0.0.1:10808", message)

    def test_log_does_not_write_query_or_bearer_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "network.log")
            with mock.patch.object(network_utils, "NETWORK_LOG", log_path):
                network_utils._write_log(
                    "request https://example.com/path?token=secret-value "
                    "Authorization=Bearer abcdef"
                )
            with open(log_path, encoding="utf-8") as handle:
                content = handle.read()

        self.assertNotIn("secret-value", content)
        self.assertNotIn("abcdef", content)
        self.assertNotIn("/path", content)


if __name__ == "__main__":
    unittest.main()
