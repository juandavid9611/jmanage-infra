"""Dobles de boto3 para tests: sin red, sin credenciales."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import safety  # noqa: E402
from seed_dev_from_prod import TABLE_OUTPUTS  # noqa: E402


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def _table_name(stack, key):
    return f"{stack}-{key}"


class FakeCF:
    def __init__(self, dev_outputs=None, prod_outputs=None, dev_stack=safety.DEV_STACK_NAME,
                 prod_stack=safety.PROD_STACK_NAME):
        self.calls = []
        outs = {}
        for stack, env, pool, over in ((dev_stack, "dev", safety.DEV_POOL_ID, dev_outputs),
                                       (prod_stack, "prod", "us-west-2_PRODPOOL", prod_outputs)):
            o = {"EnvUsed": env, "UserPoolId": pool}
            for key, out in TABLE_OUTPUTS.items():
                if out:
                    o[out] = _table_name(stack, key)
            o.update(over or {})
            outs[stack] = o
        self._outs = outs
        self._stacks = (dev_stack, prod_stack)

    def describe_stacks(self, StackName):
        self.calls.append("describe_stacks")
        outs = self._outs[StackName]
        return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outs.items()]}]}

    def get_paginator(self, name):
        cf = self

        class P:
            def paginate(self, StackName):
                cf.calls.append("list_stack_resources")
                return [{"StackResourceSummaries": [{
                    "ResourceType": "AWS::DynamoDB::Table",
                    "LogicalResourceId": "Donation1A2B3C4D",
                    "PhysicalResourceId": _table_name(StackName, "donation"),
                }]}]
        return P()


class FakeTable:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def scan(self, **kwargs):
        items = self.store.data.get(self.name, [])
        if "ExclusiveStartKey" in kwargs:
            return {"Items": items[1:]}
        if len(items) > 1:  # fuerza paginacion
            return {"Items": items[:1], "LastEvaluatedKey": {"k": 1}}
        return {"Items": list(items)}

    def get_item(self, Key):
        for i in self.store.data.get(self.name, []):
            if all(i.get(k) == v for k, v in Key.items()):
                return {"Item": i}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression:
            attr = ConditionExpression.split("(")[1].rstrip(")")
            if any(i.get(attr) == Item[attr] for i in self.store.data.get(self.name, [])):
                raise FakeClientError("ConditionalCheckFailedException")
        self.store.puts.append((self.name, Item))
        keys = ["PK", "SK"] if "PK" in Item else ["pk", "sk"] if "pk" in Item else ["id"]
        rows = self.store.data.setdefault(self.name, [])
        for idx, row in enumerate(rows):
            if all(row.get(k) == Item.get(k) for k in keys):
                rows[idx] = Item
                return
        rows.append(Item)

    def batch_writer(self, overwrite_by_pkeys=None):
        table = self

        class BW:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def put_item(self_, Item):
                table.store.puts.append((table.name, Item))
                keys = overwrite_by_pkeys or ["id"]
                rows = table.store.data.setdefault(table.name, [])
                for idx, row in enumerate(rows):
                    if all(row.get(k) == Item.get(k) for k in keys):
                        rows[idx] = Item
                        return
                rows.append(Item)

            def delete_item(self_, Key):
                table.store.deletes.append((table.name, Key))
                rows = table.store.data.get(table.name, [])
                rows[:] = [r for r in rows if not all(r.get(k) == v for k, v in Key.items())]
        return BW()


class FakeDynamo:
    def __init__(self, data=None):
        self.data = data or {}
        self.puts = []
        self.deletes = []
        self.tables_requested = []

    def Table(self, name):
        self.tables_requested.append(name)
        return FakeTable(name, self)


class FakeS3:
    def __init__(self, missing=()):
        self.copies = []
        self.objects = {}
        self.deleted = []
        self.missing = set(missing)

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = (Bucket, Body, ContentType)

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)]}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)
            self.deleted.append(o["Key"])

    def copy_object(self, CopySource, Bucket, Key):
        if CopySource["Key"] in self.missing:
            raise FakeClientError("NoSuchKey")
        self.copies.append((CopySource["Key"], Key))


class FakeCognito:
    def __init__(self, existing=()):
        self.users = {e: f"sub-{e}" for e in existing}
        self.created = []
        self.passwords = 0
        self.pools = []

    def admin_get_user(self, UserPoolId, Username):
        self.pools.append(UserPoolId)
        if Username not in self.users:
            raise FakeClientError("UserNotFoundException")
        return {"UserAttributes": [{"Name": "sub", "Value": self.users[Username]}]}

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction):
        self.pools.append(UserPoolId)
        self.users[Username] = f"sub-{Username}"
        self.created.append(Username)
        return {"User": {"Attributes": [{"Name": "sub", "Value": self.users[Username]}]}}

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        self.pools.append(UserPoolId)
        self.passwords += 1


class FakeSession:
    def __init__(self, cf, ddb, s3=None, cognito=None):
        self._c = {"cloudformation": cf, "s3": s3 or FakeS3(), "cognito-idp": cognito or FakeCognito()}
        self._ddb = ddb

    def client(self, name, region_name=None):
        return self._c[name]

    def resource(self, name, region_name=None):
        return self._ddb
