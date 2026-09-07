import json
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.views.generic import View

from apps.audit.services import record_event
from libs import Argument, JsonParser, auth, json_response

from .models import TopologyEdge, TopologyNode
from .services import (
    build_topology_graph,
    node_visible,
    source_options,
    sync_runtime_topology,
    topology_schema,
    validate_node_source,
    visible_topology_nodes,
)


def _validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(
            '%s: %s' % (field, ', '.join(messages))
            for field, messages in exc.message_dict.items()
        )
    return '; '.join(exc.messages)


def _json_object(value, field_name):
    if value in (None, ''):
        return '{}', None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True), None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None, '%s 必须是有效 JSON 对象' % field_name
        if isinstance(decoded, dict):
            return json.dumps(decoded, ensure_ascii=False, sort_keys=True), None
    return None, '%s 必须是有效 JSON 对象' % field_name


def _uuid_value(value, message):
    try:
        return uuid.UUID(str(value)), None
    except (TypeError, ValueError):
        return None, message


def _node_or_error(user, node_id):
    pk, error = _uuid_value(node_id, '拓扑节点 ID 格式错误')
    if error:
        return None, error
    node = TopologyNode.objects.filter(pk=pk).first()
    if not node:
        return None, '未找到指定拓扑节点'
    if not node_visible(user, node):
        return None, '无权操作该拓扑节点'
    return node, None


def _edge_or_error(user, edge_id):
    pk, error = _uuid_value(edge_id, '拓扑连线 ID 格式错误')
    if error:
        return None, error
    edge = TopologyEdge.objects.select_related('source', 'target').filter(
        pk=pk
    ).first()
    if not edge:
        return None, '未找到指定拓扑连线'
    if not node_visible(user, edge.source) or not node_visible(user, edge.target):
        return None, '无权操作该拓扑连线'
    return edge, None


class TopologySchemaView(View):
    @auth('topology.topology.view|topology.topology.manage')
    def get(self, request):
        return json_response(topology_schema())


class TopologyGraphView(View):
    @auth('topology.topology.view|topology.topology.manage')
    def get(self, request):
        return json_response(build_topology_graph(request.user))


class TopologySourceView(View):
    @auth('topology.topology.view|topology.topology.manage')
    def get(self, request):
        return json_response(source_options(request.user))


class TopologyRuntimeScanView(View):
    @auth('topology.topology.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('host_ids', type=list, filter=lambda x: 0 < len(x) <= 10, help='请选择需要扫描的主机'),
            Argument('dry_run', type=bool, default=False),
            Argument('selected_service_keys', type=list, required=False),
            Argument('selected_connection_keys', type=list, required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        try:
            result = sync_runtime_topology(
                request.user,
                form.host_ids,
                dry_run=form.dry_run,
                selected_service_keys=form.selected_service_keys,
                selected_connection_keys=form.selected_connection_keys,
            )
        except ValueError as exc:
            return json_response(error=str(exc))
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='topology.runtime.preview' if form.dry_run else 'topology.runtime.scan',
            resource_type='topology_runtime',
            resource_id='batch',
            result='succeeded',
            details={
                'host_ids': [item.get('host_id') for item in result],
                'succeeded': len([item for item in result if item.get('status') == 'succeeded']),
                'failed': len([item for item in result if item.get('status') == 'failed']),
            },
            request=request,
        )
        return json_response(result)


class TopologyNodeView(View):
    @auth('topology.topology.view|topology.topology.manage')
    def get(self, request):
        return json_response([
            item.to_view() for item in visible_topology_nodes(
                request.user, active_only=False
            )
        ])

    @auth('topology.topology.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', required=False),
            Argument('key', handler=str.strip, help='请输入节点唯一标识'),
            Argument('type', filter=lambda x: x in dict(TopologyNode.TYPES), help='请选择节点类型'),
            Argument('name', handler=str.strip, help='请输入节点名称'),
            Argument('description', required=False, default=''),
            Argument('source_type', default='manual', filter=lambda x: x in dict(TopologyNode.SOURCE_TYPES), help='请选择来源类型'),
            Argument('source_id', required=False, default=''),
            Argument('status', default='unknown', filter=lambda x: x in dict(TopologyNode.STATUSES), help='请选择节点状态'),
            Argument('status_source', default='manual', filter=lambda x: x in dict(TopologyNode.STATUS_SOURCES), help='请选择状态来源'),
            Argument('metadata', type=dict, default={}),
            Argument('position', type=dict, default={}),
            Argument('is_active', type=bool, default=True),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        node = None
        if form.id:
            node, error = _node_or_error(request.user, form.id)
            if error:
                return json_response(error=error)
        source_id = str(form.source_id).strip() if form.source_id else ''
        if form.source_type in ('manual', 'external', 'runtime') and not source_id:
            source_id = None
        ok, error = validate_node_source(
            request.user, form.source_type, source_id
        )
        if not ok:
            return json_response(error=error)
        metadata, error = _json_object(form.metadata, 'metadata')
        if error:
            return json_response(error=error)
        position, error = _json_object(form.position, 'position')
        if error:
            return json_response(error=error)
        if TopologyNode.objects.filter(key=form.key).exclude(
                pk=node.pk if node else None).exists():
            return json_response(error='节点唯一标识已存在')
        if node is None:
            node = TopologyNode(created_by=request.user)
        else:
            node.updated_by = request.user
            node.updated_at = datetime.now()
        node.key = form.key
        node.type = form.type
        node.name = form.name
        node.description = form.description or None
        node.source_type = form.source_type
        node.source_id = source_id
        node.status = form.status
        node.status_source = form.status_source
        node.metadata = metadata
        node.position = position
        node.is_active = form.is_active
        try:
            node.full_clean()
            node.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='topology.node.write',
            resource_type='topology_node',
            resource_id=str(node.id),
            result='succeeded',
            details={
                'key': node.key,
                'type': node.type,
                'source_type': node.source_type,
                'source_id': node.source_id,
                'status': node.status,
            },
            request=request,
        )
        return json_response(node.to_view())

    @auth('topology.topology.manage')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', help='请指定拓扑节点')
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        node, error = _node_or_error(request.user, form.id)
        if error:
            return json_response(error=error)
        node_id = str(node.id)
        node_key = node.key
        node.delete()
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='topology.node.delete',
            resource_type='topology_node',
            resource_id=node_id,
            result='succeeded',
            details={'key': node_key},
            request=request,
        )
        return json_response()


class TopologyEdgeView(View):
    @auth('topology.topology.view|topology.topology.manage')
    def get(self, request):
        node_ids = {item.id for item in visible_topology_nodes(
            request.user, active_only=False
        )}
        edges = TopologyEdge.objects.filter(
            source_id__in=node_ids,
            target_id__in=node_ids,
        )
        return json_response([item.to_view() for item in edges])

    @auth('topology.topology.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', required=False),
            Argument('source', help='请选择源节点'),
            Argument('target', help='请选择目标节点'),
            Argument('type', filter=lambda x: x in dict(TopologyEdge.TYPES), help='请选择连线类型'),
            Argument('label', required=False, default=''),
            Argument('status', default='unknown', filter=lambda x: x in dict(TopologyEdge.STATUSES), help='请选择连线状态'),
            Argument('status_source', default='manual', filter=lambda x: x in dict(TopologyEdge.STATUS_SOURCES), help='请选择状态来源'),
            Argument('probed', type=bool, default=False),
            Argument('metadata', type=dict, default={}),
            Argument('is_active', type=bool, default=True),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        edge = None
        if form.id:
            edge, error = _edge_or_error(request.user, form.id)
            if error:
                return json_response(error=error)
        source, error = _node_or_error(request.user, form.source)
        if error:
            return json_response(error=error)
        target, error = _node_or_error(request.user, form.target)
        if error:
            return json_response(error=error)
        metadata, error = _json_object(form.metadata, 'metadata')
        if error:
            return json_response(error=error)
        if TopologyEdge.objects.filter(
                source=source, target=target, type=form.type).exclude(
                    pk=edge.pk if edge else None).exists():
            return json_response(error='相同源节点、目标节点和关系类型的连线已存在')
        if edge is None:
            edge = TopologyEdge(created_by=request.user)
        else:
            edge.updated_by = request.user
            edge.updated_at = datetime.now()
        edge.source = source
        edge.target = target
        edge.type = form.type
        edge.label = form.label or None
        edge.status = form.status
        edge.status_source = form.status_source
        edge.probed = form.probed
        edge.metadata = metadata
        edge.is_active = form.is_active
        try:
            edge.full_clean()
            edge.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='topology.edge.write',
            resource_type='topology_edge',
            resource_id=str(edge.id),
            result='succeeded',
            details={
                'source': str(edge.source_id),
                'target': str(edge.target_id),
                'type': edge.type,
                'status': edge.status,
            },
            request=request,
        )
        return json_response(edge.to_view())

    @auth('topology.topology.manage')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', help='请指定拓扑连线')
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        edge, error = _edge_or_error(request.user, form.id)
        if error:
            return json_response(error=error)
        edge_id = str(edge.id)
        details = {
            'source': str(edge.source_id),
            'target': str(edge.target_id),
            'type': edge.type,
        }
        edge.delete()
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='topology.edge.delete',
            resource_type='topology_edge',
            resource_id=edge_id,
            result='succeeded',
            details=details,
            request=request,
        )
        return json_response()
