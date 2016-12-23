# -*- coding: utf-8 -*-

#    Copyright 2013 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock
from oslo_serialization import jsonutils

from nailgun.db.sqlalchemy.models import Notification

from nailgun import consts
from nailgun.rpc.receiver import NailgunReceiver
from nailgun.test.base import BaseIntegrationTest
from nailgun.test.base import fake_tasks
from nailgun.utils import reverse


class TestResetEnvironment(BaseIntegrationTest):

    @fake_tasks(
        override_state={"progress": 100, "status": "ready"},
        recover_nodes=False,
        ia_nodes_count=1
    )
    def test_reset_environment(self):
        cluster_db = self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"name": "First",
                 "pending_addition": True},
                {"name": "Second",
                 "roles": ["compute"],
                 "pending_addition": True}
            ]
        )
        supertask = self.env.launch_deployment()
        self.assertEqual(supertask.status, consts.TASK_STATUSES.ready)

        for n in cluster_db.nodes:
            self.assertEqual(n.status, "ready")
            self.assertEqual(n.pending_addition, False)

        reset_task = self.env.reset_environment()
        self.assertEqual(reset_task.status, consts.TASK_STATUSES.ready)

        self.assertEqual(cluster_db.status, "new")

        # FIXME(aroma): remove when stop action will be reworked for ha
        # cluster. To get more details, please, refer to [1]
        # [1]: https://bugs.launchpad.net/fuel/+bug/1529691
        self.assertFalse(
            cluster_db.attributes.generated['deployed_before']['value'])

        for n in cluster_db.nodes:
            self.assertEqual(n.status, "discover")
            self.assertEqual(n.pending_addition, True)
            self.assertEqual(n.roles, [])
            self.assertNotEqual(n.pending_roles, [])

        msg = (
            u"Fuel couldn't reach these nodes during "
            u"environment resetting: '{0}'. Manual "
            u"check may be needed."
        )

        self.assertEqual(
            self.db.query(Notification).filter(
                Notification.topic == "warning"
            ).filter(
                Notification.message.in_([
                    msg.format("First"),
                    msg.format("Second")
                ])
            ).count(),
            1
        )

    @fake_tasks(
        override_state={"progress": 100, "status": "ready"},
        recover_nodes=False,
        ia_nodes_count=1
    )
    def test_environment_reset_cleans_up_plugin_links(self):
        cluster_db = self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"name": "First",
                 "pending_addition": True},
                {"name": "Second",
                 "roles": ["compute"],
                 "pending_addition": True}
            ]
        )
        self.app.post(
            reverse('ClusterPluginLinkCollectionHandler',
                    kwargs={'cluster_id': cluster_db.id}),
            jsonutils.dumps({
                'title': 'simple link',
                'description': 'simple description',
                'url': 'http://example.com',
            }),
            headers=self.default_headers
        )
        self.env.launch_deployment()
        self.env.reset_environment()
        response = self.app.get(
            reverse('ClusterPluginLinkCollectionHandler',
                    kwargs={'cluster_id': cluster_db.id}),
            headers=self.default_headers)
        self.assertEqual(response.status_int, 200)
        self.assertEqual(response.json, [])

    @fake_tasks(
        override_state={"progress": 100, "status": "ready"},
        recover_nodes=False,
        ia_nodes_count=1
    )
    def test_reset_node_pending_statuses(self):
        cluster_db = self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"pending_addition": True},
            ]
        )
        node_db = self.env.nodes[0]

        # deploy environment
        deploy_task = self.env.launch_deployment()
        self.assertEqual(deploy_task.status, consts.TASK_STATUSES.ready)

        # mark node as pending_deletion
        self.app.put(
            reverse('NodeCollectionHandler'),
            jsonutils.dumps([{
                'id': node_db.id,
                'cluster_id': cluster_db.id,
                'pending_deletion': True,
            }]),
            headers=self.default_headers
        )

        # reset environment
        reset_task = self.env.reset_environment()
        self.assertEqual(reset_task.status, consts.TASK_STATUSES.ready)

        # check node statuses
        self.env.refresh_nodes()
        self.assertEqual(node_db.pending_addition, True)
        self.assertEqual(node_db.pending_deletion, False)

    @fake_tasks(
        override_state={"progress": 100, "status": "ready"},
        recover_nodes=False,
        ia_nodes_count=1
    )
    def test_reset_environment_tasks(self):
        cluster_db = self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"name": "First",
                 "pending_addition": True},
                {"name": "Second",
                 "roles": ["compute"],
                 "pending_addition": True}
            ]
        )
        supertask = self.env.launch_deployment()
        self.assertEqual(supertask.status, consts.TASK_STATUSES.ready)

        for n in cluster_db.nodes:
            self.assertEqual(n.status, "ready")
            self.assertEqual(n.pending_addition, False)

        with mock.patch('nailgun.task.task.rpc.cast') as cast_mock:
            self.env.reset_environment()
            casted_tasks = cast_mock.call_args[0][1]
            self.assertEqual(len(casted_tasks), 3)

            self.assertEqual(casted_tasks[0]['method'], 'reset_environment')
            self.assertEqual(casted_tasks[1]['method'], 'execute_tasks')
            self.assertEqual(casted_tasks[2]['method'], 'execute_tasks')

    @fake_tasks(fake_rpc=False, mock_rpc=True)
    def test_reset_environment_after_stop(self, _):
        self.cluster = self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"name": "First",
                 "pending_addition": True},
                {"name": "Second",
                 "roles": ["compute"],
                 "pending_addition": True}
            ]
        )
        self.emulate_nodes_provisioning(self.env.nodes)
        supertask = self.env.launch_deployment()
        deploy_task = [t for t in supertask.subtasks
                       if t.name in (consts.TASK_NAMES.deployment)][0]

        NailgunReceiver.deploy_resp(
            task_uuid=deploy_task.uuid,
            status=consts.TASK_STATUSES.running,
            progress=50,
            nodes=[{'uid': n.uid, 'progress': 10} for n in self.env.nodes]
        )

        stop_task = self.env.stop_deployment()
        NailgunReceiver.stop_deployment_resp(
            task_uuid=stop_task.uuid,
            status=consts.TASK_STATUSES.ready,
            progress=100,
            nodes=[{'uid': n.uid} for n in self.env.nodes],
        )

        reset_task = self.env.reset_environment()

        NailgunReceiver.reset_environment_resp(
            task_uuid=reset_task.uuid,
            status=consts.TASK_STATUSES.ready,
            progress=100,
            nodes=[{'uid': n.uid} for n in self.env.nodes]
        )

        self.assertEqual(reset_task.status, consts.TASK_STATUSES.ready)

        self.assertEqual(self.cluster.status, "new")

        # FIXME(aroma): remove when stop action will be reworked for ha
        # cluster. To get more details, please, refer to [1]
        # [1]: https://bugs.launchpad.net/fuel/+bug/1529691
        self.assertFalse(
            self.cluster.attributes.generated['deployed_before']['value'])

        for n in self.env.nodes:
            self.assertEqual(n.status, "discover")
            self.assertEqual(n.pending_addition, True)
            self.assertEqual(n.roles, [])
            self.assertNotEqual(n.pending_roles, [])
            self.assertEqual(n.progress, 0)
