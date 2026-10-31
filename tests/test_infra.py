"""Template invariants that cfn-lint can't know about."""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: {
    suffix: loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
    else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node)})


def _resources(name):
    return yaml.load((ROOT / "infra" / name).read_text(), Loader=_Loader)["Resources"]


def test_every_cognito_app_client_has_managed_login_branding():
    # The user pool domain uses managed login v2, which refuses to render the
    # sign-in page ("Login pages unavailable") for clients without a style.
    foundation = _resources("foundation.yaml")
    domain = next(r for r in foundation.values() if r["Type"] == "AWS::Cognito::UserPoolDomain")
    assert domain["Properties"].get("ManagedLoginVersion") == 2

    app = _resources("app.yaml")
    clients = {k for k, r in app.items() if r["Type"] == "AWS::Cognito::UserPoolClient"}
    branded = {r["Properties"]["ClientId"]["Ref"] for r in app.values()
               if r["Type"] == "AWS::Cognito::ManagedLoginBranding"}
    assert clients and clients <= branded
