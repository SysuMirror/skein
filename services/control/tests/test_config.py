import json

from skein.config import Config
from skein.forge import RepoTarget


def test_platform_repos_parsed() -> None:
    config = Config.from_env({"SKEIN_PLATFORM_REPOS": '{"forum": "/srv/repo"}'})
    assert config.platform_repos == {"forum": "/srv/repo"}


def test_platform_repos_invalid_json_is_empty() -> None:
    assert Config.from_env({"SKEIN_PLATFORM_REPOS": "not-json"}).platform_repos == {}


def test_platform_repos_default_empty() -> None:
    assert Config.from_env({}).platform_repos == {}


def test_control_url_parsed() -> None:
    config = Config.from_env({"SKEIN_CONTROL_URL": "http://127.0.0.1:7800"})
    assert config.control_url == "http://127.0.0.1:7800"


def test_github_config_parsed() -> None:
    config = Config.from_env(
        {
            "SKEIN_GITHUB_TOKEN": "tok",
            "SKEIN_GITHUB_REPOS": '{"skein": "o/r"}',
            "SKEIN_GITHUB_BASE": "develop",
            "SKEIN_GITHUB_PR_DRAFT": "0",
        }
    )
    assert config.github_token == "tok"
    assert config.github_repos == {"skein": RepoTarget(slug="o/r")}
    assert config.github_base == "develop"
    assert config.github_pr_draft is False


def test_github_repos_dict_form_and_base() -> None:
    config = Config.from_env(
        {
            "SKEIN_GITHUB_REPOS": json.dumps(
                {
                    "market": {"slug": "example-org/example-service", "base": "develop"},
                    "mirror": {"slug": "example-org/example-mirror"},
                }
            )
        }
    )
    assert config.github_repos["market"] == RepoTarget(
        slug="example-org/example-service", base="develop"
    )
    assert config.github_repos["mirror"] == RepoTarget(slug="example-org/example-mirror")


def test_github_base_defaults_to_auto() -> None:
    assert Config.from_env({}).github_base == ""


def test_test_envs_parsed() -> None:
    config = Config.from_env(
        {"SKEIN_TEST_ENVS": json.dumps({"example-org/example-mirror": "https://mirror-test.example"})}
    )
    assert config.test_envs == {"example-org/example-mirror": "https://mirror-test.example"}


def test_test_envs_invalid_json_is_empty() -> None:
    assert Config.from_env({"SKEIN_TEST_ENVS": "nope"}).test_envs == {}


def test_github_repos_invalid_json_is_empty() -> None:
    assert Config.from_env({"SKEIN_GITHUB_REPOS": "nope"}).github_repos == {}
