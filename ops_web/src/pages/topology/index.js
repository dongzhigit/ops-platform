import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Descriptions, Drawer, Empty, Form, Input, List, Modal, Radio,
  Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons';

import { Action, AuthButton, AuthDiv, Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';
import styles from './index.module.less';


const statusMeta = {
  healthy: {label: '正常', color: 'green'},
  info: {label: '提示', color: 'blue'},
  warning: {label: '警告', color: 'orange'},
  critical: {label: '严重', color: 'red'},
  offline: {label: '离线', color: 'red'},
  disabled: {label: '停用', color: 'default'},
  unknown: {label: '未知', color: 'default'},
};

const nodeIcon = {
  host: <CloudServerOutlined/>,
  application: <AppstoreOutlined/>,
  service: <ApiOutlined/>,
  process: <DeploymentUnitOutlined/>,
  port: <LinkOutlined/>,
  database: <DatabaseOutlined/>,
  middleware: <DeploymentUnitOutlined/>,
  external: <LinkOutlined/>,
  monitor_target: <DeploymentUnitOutlined/>,
};

const sourceTypeLabel = {
  manual: '人工维护',
  host: '主机资产',
  application: '应用',
  service: '配置服务',
  metric_target: '监控采集目标',
  runtime: '运行态快照',
  external: '外部系统',
  summary: '聚合视图',
};

const confidenceLabel = {low: '低', medium: '中', high: '高'};
const riskColor = {low: 'green', medium: 'blue', high: 'orange', critical: 'red'};
const NODE_WIDTH = 188;
const NODE_HEIGHT = 44;
const COLUMN_SPACING = 340;
const ROW_SPACING = 72;
const EDGE_LABEL_CHARS = 14;
const EDGE_LABEL_LINE_HEIGHT = 14;
const EDGE_ANCHOR_MAX_OFFSET = Math.max(4, NODE_HEIGHT / 2 - 7);
const EDGE_CONTROL_MAX_OFFSET = 58;
const ZOOM_OPTIONS = [0.6, 0.8, 1, 1.25, 1.5, 2];


function CitationTags({values}) {
  if (!values || !values.length) return <Typography.Text type="secondary">无引用</Typography.Text>;
  return <>{values.map(value => <Tag key={value} color="blue">{value}</Tag>)}</>;
}


function TopologyDiagnosisDrawer({visible, record, onClose}) {
  const result = record && record.result ? record.result : {};
  const evidence = record && record.evidence ? record.evidence : [];
  return (
    <Drawer visible={visible} width={920} title="AI 拓扑诊断" onClose={onClose}>
      {record ? (
        <Space direction="vertical" size="middle" style={{width: '100%'}}>
          <Alert
            showIcon
            type={record.status === 'failed' ? 'error' : 'warning'}
            message={record.status === 'failed' ? record.error : 'AI 仅基于当前授权证据给出只读诊断，未执行任何命令或变更。'}/>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="状态">{record.status}</Descriptions.Item>
            <Descriptions.Item label="模型">{record.model}</Descriptions.Item>
            <Descriptions.Item label="关联 ID" span={2}>
              <Typography.Text copyable>{record.correlation_id}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="问题" span={2}>{record.question}</Descriptions.Item>
          </Descriptions>
          {record.status === 'completed' && (
            <>
              <Typography.Title level={4}>诊断摘要</Typography.Title>
              <Typography.Paragraph>{result.summary}</Typography.Paragraph>
              <Typography.Title level={4}>影响范围</Typography.Title>
              <List
                dataSource={result.impact_scope || []}
                locale={{emptyText: '暂无'}}
                renderItem={item => <List.Item>{item}</List.Item>}/>
              <Typography.Title level={4}>可能故障点</Typography.Title>
              <List
                dataSource={result.likely_fault_points || []}
                locale={{emptyText: '暂无'}}
                renderItem={item => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space>
                        <span>{item.target}</span>
                        <Tag>{confidenceLabel[item.confidence] || item.confidence}置信度</Tag>
                      </Space>}
                      description={<div><div>{item.reason}</div><CitationTags values={item.citations}/></div>}/>
                  </List.Item>
                )}/>
              <Typography.Title level={4}>事实</Typography.Title>
              <List
                dataSource={result.facts || []}
                locale={{emptyText: '暂无'}}
                renderItem={item => (
                  <List.Item><div><div>{item.statement}</div><CitationTags values={item.citations}/></div></List.Item>
                )}/>
              <Typography.Title level={4}>未知项</Typography.Title>
              <List
                dataSource={result.unknowns || []}
                locale={{emptyText: '暂无'}}
                renderItem={item => <List.Item>{item}</List.Item>}/>
              <Typography.Title level={4}>建议</Typography.Title>
              <List
                dataSource={result.recommendations || []}
                locale={{emptyText: '暂无'}}
                renderItem={item => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space>
                        <span>{item.title}</span>
                        <Tag color={riskColor[item.risk_level]}>{item.risk_level}</Tag>
                      </Space>}
                      description={<div><div>{item.rationale}</div><CitationTags values={item.citations}/></div>}/>
                  </List.Item>
                )}/>
            </>
          )}
          <Typography.Title level={4}>证据</Typography.Title>
          <List
            dataSource={evidence}
            locale={{emptyText: '暂无'}}
            renderItem={item => (
              <List.Item>
                <List.Item.Meta
                  title={<Space><Tag>{item.type}</Tag><span>{item.title}</span></Space>}
                  description={<Typography.Text code>{item.citation}</Typography.Text>}/>
              </List.Item>
            )}/>
        </Space>
      ) : <Empty/>}
    </Drawer>
  );
}


function enumMap(values) {
  const result = {};
  (values || []).forEach(item => {
    result[item.key] = item.name;
  });
  return result;
}


function splitEdgeLabel(text) {
  const chars = Array.from(text || '--');
  const lines = [];
  for (let index = 0; index < chars.length; index += EDGE_LABEL_CHARS) {
    lines.push(chars.slice(index, index + EDGE_LABEL_CHARS).join(''));
  }
  return lines.length ? lines : ['--'];
}


function labelTextWidth(text) {
  return Array.from(text || '').reduce((value, char) => (
    value + (char.charCodeAt(0) > 255 ? 12 : 7)
  ), 0);
}


function nodeMetaText(node) {
  const metadata = node.metadata || {};
  if (node.type === 'process') {
    return [
      metadata.framework || metadata.service || metadata.process_name || '进程',
      metadata.pid ? `PID ${metadata.pid}` : null,
    ].filter(Boolean).join(' · ');
  }
  if (['port', 'database', 'middleware'].includes(node.type)) {
    return [
      metadata.service || metadata.role || metadata.protocol || node.type,
      metadata.port ? `:${metadata.port}` : null,
    ].filter(Boolean).join(' ');
  }
  if (node.type === 'host') {
    return metadata.hostname || metadata.address || node.key || '主机';
  }
  if (node.type === 'external') {
    return metadata.address || node.key || '外部';
  }
  return metadata.service || metadata.framework || node.key || node.type;
}


const statusRank = {
  critical: 60,
  offline: 55,
  warning: 50,
  info: 40,
  disabled: 30,
  healthy: 20,
  unknown: 10,
};


function highestStatus(values) {
  return (values || []).filter(Boolean).sort((a, b) => (
    (statusRank[b] || 0) - (statusRank[a] || 0)
  ))[0] || 'unknown';
}


function getRuntimeHostId(node) {
  if (!node) return null;
  if (node.source && node.source.type === 'host') return String(node.source.id);
  if (node.source && node.source.type === 'runtime' && node.source.id) return String(node.source.id);
  if (node.metadata && node.metadata.host_id) return String(node.metadata.host_id);
  return null;
}


function getBusinessTargetHostId(node) {
  if (!node) return null;
  const metadata = node.metadata || {};
  if (metadata.remote_host_id) return String(metadata.remote_host_id);
  return getRuntimeHostId(node);
}


function getHostNodeById(nodes) {
  const result = {};
  (nodes || []).forEach(node => {
    if (node.is_active && node.type === 'host' && node.source && node.source.type === 'host') {
      result[String(node.source.id)] = node;
    }
  });
  return result;
}


function syntheticEdge(id, source, target, type, label, status, metadata = {}) {
  return {
    id,
    source,
    target,
    type,
    label,
    status,
    status_source: metadata.status_source || 'runtime',
    probed: metadata.probed !== false,
    metadata,
    is_active: true,
  };
}


function summarizeEdges(items) {
  const groups = {};
  items.forEach(edge => {
    const key = [edge.source, edge.target, edge.type].join('|');
    if (!groups[key]) {
      groups[key] = {...edge, count: 0, statuses: [], labels: []};
    }
    groups[key].count += edge.metadata && edge.metadata.count ? edge.metadata.count : 1;
    groups[key].statuses.push(edge.status);
    if (edge.label) groups[key].labels.push(edge.label);
  });
  return Object.values(groups).map(edge => ({
    ...edge,
    status: highestStatus(edge.statuses),
    label: edge.count > 1 && edge.labels[0]
      ? `${edge.count} 条连接 · ${edge.labels[0]}`
      : (edge.count > 1 ? `${edge.count} 条连接` : (edge.labels[0] || edge.label)),
    metadata: {...(edge.metadata || {}), count: edge.count, labels: edge.labels.slice(0, 12)},
  }));
}


function buildServerGraph(nodes, edges) {
  const activeNodes = (nodes || []).filter(item => item.is_active);
  const activeEdges = (edges || []).filter(item => item.is_active);
  const hostBySourceId = getHostNodeById(activeNodes);
  const resultNodes = {};
  const resultEdges = [];

  activeNodes.forEach(node => {
    if (node.type === 'host' && node.source && node.source.type === 'host') {
      resultNodes[node.id] = node;
    }
  });

  activeEdges.forEach(edge => {
    if (edge.type !== 'calls') return;
    const source = activeNodes.find(item => item.id === edge.source);
    const target = activeNodes.find(item => item.id === edge.target);
    if (!source || !target) return;
    const sourceHostId = getRuntimeHostId(source);
    const sourceHost = hostBySourceId[sourceHostId];
    if (!sourceHost) return;
    const targetHostId = getBusinessTargetHostId(target);
    const targetHost = targetHostId && hostBySourceId[targetHostId];
    if (!targetHost || targetHost.id === sourceHost.id) return;
    resultEdges.push(syntheticEdge(
      `summary:${edge.id}:host`,
      sourceHost.id,
      targetHost.id,
      'calls',
      edge.label,
      edge.status,
      {
        source_edge: edge.id,
        status_source: edge.status_source,
        probed: edge.probed,
        service_label: edge.label,
      },
    ));
  });

  return {
    nodes: Object.values(resultNodes),
    edges: summarizeEdges(resultEdges),
    order: ['host'],
  };
}


function buildRuntimeGraph(nodes, edges, selectedHostId) {
  const activeNodes = (nodes || []).filter(item => item.is_active);
  const activeEdges = (edges || []).filter(item => item.is_active);
  const hostBySourceId = getHostNodeById(activeNodes);
  const hostNode = selectedHostId ? hostBySourceId[String(selectedHostId)] : null;
  if (!hostNode) return {nodes: [], edges: [], order: ['process', 'port', 'database', 'middleware', 'external', 'host']};

  const nodeMap = {};
  function addServiceNode(node, fallbackHost = false) {
    if (!node) return;
    if (node.type === 'monitor_target') return;
    if (node.type === 'host' && !fallbackHost) return;
    nodeMap[node.id] = node;
  }

  activeNodes.forEach(node => {
    if (
      getRuntimeHostId(node) === String(selectedHostId) &&
      ['port', 'database', 'middleware', 'external'].includes(node.type)
    ) {
      addServiceNode(node);
    }
  });

  const runtimeEdges = [];
  activeEdges.forEach(edge => {
    if (!['listens_on', 'calls'].includes(edge.type)) return;
    const source = activeNodes.find(item => item.id === edge.source);
    const target = activeNodes.find(item => item.id === edge.target);
    if (!source || !target) return;
    const sourceHost = getRuntimeHostId(source);
    const targetHost = getRuntimeHostId(target);
    const targetBusinessHost = getBusinessTargetHostId(target);
    if (
      sourceHost !== String(selectedHostId) &&
      targetHost !== String(selectedHostId) &&
      targetBusinessHost !== String(selectedHostId)
    ) return;
    addServiceNode(source, edge.type === 'calls');
    addServiceNode(target, edge.type === 'calls');
    if (nodeMap[source.id] && nodeMap[target.id]) runtimeEdges.push(edge);
  });

  return {
    nodes: Object.values(nodeMap),
    edges: runtimeEdges,
    order: ['process', 'port', 'database', 'middleware', 'external', 'host'],
  };
}


function applyStatusFilter(nodes, edges, filter) {
  if (filter === 'all') return {nodes, edges};
  const visibleIds = new Set(
    nodes.filter(item => item.is_active && item.status === filter).map(item => item.id)
  );
  edges.forEach(edge => {
    if (visibleIds.has(edge.source) || visibleIds.has(edge.target)) {
      visibleIds.add(edge.source);
      visibleIds.add(edge.target);
    }
  });
  return {
    nodes: nodes.filter(item => visibleIds.has(item.id)),
    edges: edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}


function buildLayout(nodes, edges, filter, mode, selectedHostId) {
  let graph = {nodes: nodes || [], edges: edges || [], order: [
    'external', 'application', 'service', 'host',
    'process', 'port', 'database', 'middleware', 'monitor_target',
  ]};
  if (mode === 'servers') graph = buildServerGraph(nodes, edges);
  if (mode === 'runtime') graph = buildRuntimeGraph(nodes, edges, selectedHostId);
  const filtered = applyStatusFilter(
    graph.nodes.filter(item => item.is_active),
    graph.edges.filter(item => item.is_active),
    filter,
  );
  const presentTypes = new Set(filtered.nodes.map(item => item.type));
  const order = graph.order.filter(type => presentTypes.has(type));
  const columns = {};
  order.forEach((type, index) => {
    columns[type] = {x: 28 + index * COLUMN_SPACING, y: 52, items: []};
  });
  filtered.nodes.forEach(node => {
    if (columns[node.type]) columns[node.type].items.push(node);
  });
  Object.values(columns).forEach(col => {
    col.items.sort((a, b) => a.name.localeCompare(b.name));
  });
  const maxRows = Math.max(
    ...Object.values(columns).map(col => col.items.length),
    1,
  );
  const positioned = [];
  Object.entries(columns).forEach(([type, col]) => {
    const startY = col.y + Math.max(0, maxRows - col.items.length) * ROW_SPACING / 2;
    col.items.forEach((item, index) => {
      positioned.push({
        ...item,
        x: col.x,
        y: startY + index * ROW_SPACING,
      });
    });
  });
  const nodeMap = {};
  positioned.forEach(item => {
    nodeMap[item.id] = item;
  });
  const visibleEdges = filtered.edges.filter(edge => (
    edge.is_active && nodeMap[edge.source] && nodeMap[edge.target]
  ));
  return {
    nodes: positioned,
    nodeMap,
    edges: routeEdges(visibleEdges, nodeMap),
    width: Math.max(760, order.length * COLUMN_SPACING + 36),
    height: Math.max(420, 118 + maxRows * ROW_SPACING),
    columns,
  };
}


function routeOffset(index, count, maxOffset) {
  if (count <= 1) return 0;
  const step = Math.min(8, (maxOffset * 2) / Math.max(count - 1, 1));
  return (index - (count - 1) / 2) * step;
}


function routeEdges(edges, nodeMap) {
  const routed = (edges || []).map(edge => ({...edge}));
  const anchorGroups = {};
  const pairGroups = {};

  function addAnchor(edge, node, other, side, attr, controlAttr) {
    const key = `${node.id}:${side}`;
    if (!anchorGroups[key]) anchorGroups[key] = [];
    anchorGroups[key].push({edge, other, attr, controlAttr});
  }

  routed.forEach(edge => {
    const source = nodeMap[edge.source];
    const target = nodeMap[edge.target];
    if (!source || !target) return;
    const forward = source.x <= target.x;
    addAnchor(
      edge, source, target, forward ? 'right' : 'left',
      'routeSourceOffset', 'routeSourceControlOffset',
    );
    addAnchor(
      edge, target, source, forward ? 'left' : 'right',
      'routeTargetOffset', 'routeTargetControlOffset',
    );

    const pairKey = `${edge.source}|${edge.target}`;
    if (!pairGroups[pairKey]) pairGroups[pairKey] = [];
    pairGroups[pairKey].push(edge);
  });

  Object.values(anchorGroups).forEach(group => {
    group.sort((a, b) => (
      a.other.y - b.other.y ||
      a.other.x - b.other.x ||
      String(a.edge.id).localeCompare(String(b.edge.id))
    ));
    const controlMaxOffset = Math.min(
      EDGE_CONTROL_MAX_OFFSET,
      Math.max(18, group.length * 3),
    );
    group.forEach((item, index) => {
      item.edge[item.attr] = routeOffset(
        index, group.length, EDGE_ANCHOR_MAX_OFFSET,
      );
      item.edge[item.controlAttr] = routeOffset(
        index, group.length, controlMaxOffset,
      );
    });
  });

  Object.values(pairGroups).forEach(group => {
    group.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    group.forEach((edge, index) => {
      edge.routeBendOffset = routeOffset(index, group.length, 22);
    });
  });

  return routed;
}


function applyDragPositions(layout, positions) {
  if (!positions || !Object.keys(positions).length) return layout;
  const nextNodes = layout.nodes.map(node => (
    positions[node.id] ? {...node, ...positions[node.id]} : node
  ));
  const nodeMap = {};
  nextNodes.forEach(node => {
    nodeMap[node.id] = node;
  });
  const width = nextNodes.reduce((value, node) => Math.max(value, node.x + NODE_WIDTH + 48), layout.width);
  const height = nextNodes.reduce((value, node) => Math.max(value, node.y + NODE_HEIGHT + 48), layout.height);
  return {...layout, nodes: nextNodes, nodeMap, edges: routeEdges(layout.edges, nodeMap), width, height};
}


function Node({node, selected, dragging, onSelect, onDragStart}) {
  const meta = statusMeta[node.status] || statusMeta.unknown;
  const shortMeta = nodeMetaText(node);
  const detail = [node.name, shortMeta, node.key, node.description].filter(Boolean).join('\n');
  return (
    <button
      type="button"
      className={`${styles.node} ${styles[node.status || 'unknown']} ${selected ? styles.selected : ''} ${dragging ? styles.dragging : ''}`}
      style={{left: node.x, top: node.y}}
      title={detail}
      onMouseDown={event => onDragStart(node, event)}
      onClick={() => onSelect(node)}>
      <span className={styles.nodeIcon}>{nodeIcon[node.type] || <DeploymentUnitOutlined/>}</span>
      <span className={styles.nodeBody}>
        <span className={styles.nodeTitle}>{node.name}</span>
        <span className={styles.nodeMeta}>{shortMeta}</span>
      </span>
      <span className={styles.nodeStatus} title={meta.label}>
        <span className={styles.nodeStatusDot}/>
      </span>
    </button>
  );
}


function Edge({edge, source, target, edgeTypes, focused}) {
  const forward = source.x <= target.x;
  const x1 = source.x + (forward ? NODE_WIDTH : 0);
  const y1 = source.y + NODE_HEIGHT / 2 + (edge.routeSourceOffset || 0);
  const x2 = target.x + (forward ? 0 : NODE_WIDTH);
  const y2 = target.y + NODE_HEIGHT / 2 + (edge.routeTargetOffset || 0);
  const bend = edge.routeBendOffset || 0;
  const controlDistance = Math.max(36, Math.min(90, Math.abs(x2 - x1) * 0.35));
  const c1x = x1 + (forward ? controlDistance : -controlDistance);
  const c2x = x2 - (forward ? controlDistance : -controlDistance);
  const c1y = y1 + bend + (edge.routeSourceControlOffset || 0);
  const c2y = y2 + bend + (edge.routeTargetControlOffset || 0);
  const label = edge.label || edgeTypes[edge.type] || '连接';
  const labelLines = splitEdgeLabel(label);
  const labelX = (x1 + x2) / 2;
  const labelHeight = labelLines.length * EDGE_LABEL_LINE_HEIGHT + 8;
  const labelWidth = Math.max(72, Math.max(...labelLines.map(labelTextWidth)) + 16);
  const labelTop = (y1 + y2) / 2 + bend - labelHeight / 2;
  return (
    <g>
      <path
        className={`${styles.edge} ${styles[edge.status || 'unknown']} ${focused ? styles.edgeFocused : ''} ${edge.probed ? '' : styles.dashed}`}
        d={`M${x1},${y1} C${c1x},${c1y} ${c2x},${c2y} ${x2},${y2}`}
        markerEnd="url(#topology-arrow)"/>
      <g className={`${styles.edgeLabelGroup} ${focused ? styles.edgeLabelFocused : ''}`}>
        <title>{label}</title>
        <rect
          className={styles.edgeLabelBg}
          x={labelX - labelWidth / 2}
          y={labelTop}
          width={labelWidth}
          height={labelHeight}
          rx="4"
          ry="4"/>
        <text x={labelX} y={labelTop + 16} textAnchor="middle" className={styles.edgeLabel}>
          {labelLines.map((line, index) => (
            <tspan key={index} x={labelX} dy={index === 0 ? 0 : EDGE_LABEL_LINE_HEIGHT}>
              {line}
            </tspan>
          ))}
        </text>
      </g>
    </g>
  );
}


function ConnectedList({node, graph, edgeTypes}) {
  const related = graph.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  if (!related.length) return <span className={styles.emptyText}>暂无可见连接</span>;
  return related.map(edge => {
    const otherId = edge.source === node.id ? edge.target : edge.source;
    const other = graph.nodeMap[otherId];
    if (!other) return null;
    const meta = statusMeta[edge.status] || statusMeta.unknown;
    return (
      <div key={edge.id} className={styles.connectionRow}>
        <span>{edgeTypes[edge.type] || edge.type}</span>
        <b>{other.name}</b>
        <Tag color={meta.color}>{edge.probed ? '已探测' : '未探测'}</Tag>
      </div>
    );
  });
}


function metricText(value, suffix = '') {
  if (value === undefined || value === null || value === '') return '--';
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return `${number.toFixed(1)}${suffix}`;
}


function ObservabilityDetail({node}) {
  const observability = node.metadata && node.metadata.observability;
  if (!observability) {
    return <span className={styles.emptyText}>暂无监控或告警证据</span>;
  }
  const target = observability.target;
  const metrics = observability.metrics || {};
  const alerts = observability.alerts || [];
  return (
    <Space direction="vertical" style={{width: '100%'}}>
      {observability.summary_error && (
        <Alert showIcon type="warning" message={observability.summary_error}/>
      )}
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="采集目标">
          {target ? `${target.scheme}://${target.exporter_address}:${target.exporter_port}${target.metrics_path}` : '未配置'}
        </Descriptions.Item>
        <Descriptions.Item label="在线状态">{metricText(metrics.availability)}</Descriptions.Item>
        <Descriptions.Item label="CPU">{metricText(metrics.cpu, '%')}</Descriptions.Item>
        <Descriptions.Item label="内存">{metricText(metrics.memory, '%')}</Descriptions.Item>
        <Descriptions.Item label="磁盘">{metricText(metrics.disk, '%')}</Descriptions.Item>
        <Descriptions.Item label="告警数量">{observability.alert_count || 0}</Descriptions.Item>
      </Descriptions>
      <div>
        {alerts.length ? alerts.slice(0, 6).map(item => (
          <Tooltip key={item.id} title={item.summary || item.description || item.alert_name}>
            <Tag color={(statusMeta[item.severity] || statusMeta.warning).color}>{item.alert_name}</Tag>
          </Tooltip>
        )) : <span className={styles.emptyText}>当前无触发告警</span>}
      </div>
      <Space>
        <Button size="small" onClick={() => window.open('/monitor', '_blank')}>监控面板</Button>
        <Button size="small" onClick={() => window.open('/alarm/alarm', '_blank')}>报警工作台</Button>
        {observability.host_id && (
          <Button size="small" onClick={() => window.open('/host', '_blank')}>主机管理</Button>
        )}
      </Space>
    </Space>
  );
}


function RuntimeDetail({node}) {
  const metadata = node.metadata || {};
  if (node.source.type !== 'runtime' && !metadata.observed_at) {
    return <span className={styles.emptyText}>暂无运行态扫描信息</span>;
  }
  return (
    <Descriptions size="small" column={1}>
      <Descriptions.Item label="采集时间">{metadata.observed_at || '--'}</Descriptions.Item>
      {metadata.role && <Descriptions.Item label="类型">{metadata.role}</Descriptions.Item>}
      {metadata.service && <Descriptions.Item label="服务">{metadata.service}</Descriptions.Item>}
      {metadata.framework && <Descriptions.Item label="框架">{metadata.framework}</Descriptions.Item>}
      {metadata.detected_by && <Descriptions.Item label="识别方式">{metadata.detected_by}</Descriptions.Item>}
      {metadata.process_name && <Descriptions.Item label="进程">{metadata.process_name}</Descriptions.Item>}
      {metadata.pid && <Descriptions.Item label="PID">{metadata.pid}</Descriptions.Item>}
      {metadata.ppid && <Descriptions.Item label="PPID">{metadata.ppid}</Descriptions.Item>}
      {metadata.stat && <Descriptions.Item label="状态">{metadata.stat}</Descriptions.Item>}
      {metadata.protocol && <Descriptions.Item label="协议">{metadata.protocol}</Descriptions.Item>}
      {metadata.port && <Descriptions.Item label="端口">{metadata.port}</Descriptions.Item>}
      {metadata.address && <Descriptions.Item label="地址">{metadata.address}</Descriptions.Item>}
      {metadata.peer && (
        <Descriptions.Item label="连接对端">
          {metadata.peer.address}:{metadata.peer.port}
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}


function sourceItems(sourceType, sources) {
  if (sourceType === 'host') return sources.hosts || [];
  if (sourceType === 'application') return sources.applications || [];
  if (sourceType === 'service') return sources.services || [];
  if (sourceType === 'metric_target') return sources.metric_targets || [];
  return [];
}


function RuntimeDiscoveryPreview({results, onSelectionChange}) {
  const services = [];
  const connections = [];
  (results || []).forEach(result => {
    const recommendations = result.recommendations || {};
    (recommendations.services || []).forEach(item => services.push({
      ...item,
      host_name: result.host_name,
    }));
    (recommendations.connections || []).forEach(item => connections.push({
      ...item,
      host_name: result.host_name,
    }));
  });
  const [selectedServiceKeys, setSelectedServiceKeys] = useState(
    services.filter(item => item.selected !== false).map(item => item.key)
  );
  const [selectedConnectionKeys, setSelectedConnectionKeys] = useState(
    connections.filter(item => item.selected !== false).map(item => item.key)
  );
  function updateServices(keys) {
    setSelectedServiceKeys(keys);
    if (onSelectionChange) onSelectionChange(keys, selectedConnectionKeys);
  }
  function updateConnections(keys) {
    setSelectedConnectionKeys(keys);
    if (onSelectionChange) onSelectionChange(selectedServiceKeys, keys);
  }
  return (
    <div>
      <Alert
        showIcon
        type="info"
        style={{marginBottom: 12}}
        message="只把勾选的服务和业务连接加入固定拓扑监控；未勾选的临时连接只作为本次发现候选。"/>
      <Table
        size="small"
        rowKey="key"
        dataSource={services}
        rowSelection={{selectedRowKeys: selectedServiceKeys, onChange: updateServices}}
        pagination={false}
        locale={{emptyText: '未发现候选服务'}}
        style={{marginBottom: 12}}>
        <Table.Column title="主机" dataIndex="host_name"/>
        <Table.Column title="服务" dataIndex="name"/>
        <Table.Column title="层级" dataIndex="runtime_layer" render={value => <Tag>{value || 'unknown'}</Tag>}/>
        <Table.Column title="识别方式" dataIndex="detected_by" render={value => value || '--'}/>
        <Table.Column title="端口" dataIndex="port"/>
      </Table>
      <Table
        size="small"
        rowKey="key"
        dataSource={connections}
        rowSelection={{selectedRowKeys: selectedConnectionKeys, onChange: updateConnections}}
        pagination={false}
        locale={{emptyText: '未发现候选连接'}}>
        <Table.Column title="主机" dataIndex="host_name"/>
        <Table.Column title="来源" dataIndex="source"/>
        <Table.Column title="目标" dataIndex="target"/>
        <Table.Column title="服务" dataIndex="service"/>
        <Table.Column title="连接数" dataIndex="count"/>
      </Table>
    </div>
  );
}


function NodeForm({visible, record, schema, sources, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [sourceType, setSourceType] = useState('manual');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible) return;
    const nextSourceType = record && record.source ? record.source.type : 'manual';
    setSourceType(nextSourceType);
    form.setFieldsValue({
      id: record && record.id,
      key: record && record.key,
      type: record && record.type ? record.type : 'service',
      name: record && record.name,
      description: record && record.description,
      source_type: nextSourceType,
      source_id: record && record.source ? record.source.id : undefined,
      status: record && record.status ? record.status : 'unknown',
      status_source: record && record.status_source ? record.status_source : 'manual',
      is_active: record ? record.is_active : true,
    });
  }, [visible, record, form]);

  function pickSource(value) {
    const selected = sourceItems(sourceType, sources).find(item => String(item.id) === String(value));
    if (!selected) return;
    const values = {
      key: selected.key,
      name: selected.name,
      description: selected.description || selected.hostname || selected.host_name || '',
    };
    if (sourceType === 'host') values.type = 'host';
    if (sourceType === 'application') values.type = 'application';
    if (sourceType === 'service') values.type = 'service';
    if (sourceType === 'metric_target') values.type = 'monitor_target';
    form.setFieldsValue(values);
  }

  function submit() {
    form.validateFields().then(values => {
      setSaving(true);
      return http.post('/api/v1/topology/nodes/', {
        ...values,
        metadata: {},
        position: {},
      });
    }).then(data => {
      message.success('拓扑节点已保存');
      onSuccess(data);
    }).finally(() => setSaving(false));
  }

  return (
    <Modal
      visible={visible}
      width={720}
      title={record && record.id ? '编辑拓扑节点' : '新增拓扑节点'}
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Form form={form} labelCol={{span: 6}} wrapperCol={{span: 16}}>
        <Form.Item name="id" hidden><Input/></Form.Item>
        <Form.Item name="source_type" label="来源类型" rules={[{required: true}]}>
          <Select onChange={value => {
            setSourceType(value);
            form.setFieldsValue({source_id: undefined});
          }}>
            {(schema.source_types || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        {!['manual', 'external', 'runtime'].includes(sourceType) && (
          <Form.Item name="source_id" label="来源对象" rules={[{required: true, message: '请选择来源对象'}]}>
            <Select showSearch optionFilterProp="children" onChange={pickSource}>
              {sourceItems(sourceType, sources).map(item => (
                <Select.Option key={item.id} value={String(item.id)}>
                  {item.name}{item.hostname ? `（${item.hostname}）` : ''}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        )}
        <Form.Item name="type" label="节点类型" rules={[{required: true}]}>
          <Select>
            {(schema.node_types || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="name" label="节点名称" rules={[{required: true, message: '请输入节点名称'}, {max: 100}]}>
          <Input/>
        </Form.Item>
        <Form.Item
          name="key"
          label="唯一标识"
          rules={[
            {required: true, message: '请输入节点唯一标识'},
            {pattern: /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/, message: '只能包含字母、数字、下划线、点、冒号和中划线'},
          ]}>
          <Input placeholder="例如 host:226 或 service:web"/>
        </Form.Item>
        <Form.Item name="description" label="描述" rules={[{max: 255}]}>
          <Input.TextArea rows={3}/>
        </Form.Item>
        <Form.Item name="status" label="状态" rules={[{required: true}]}>
          <Select>
            {(schema.statuses || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="status_source" label="状态来源" rules={[{required: true}]}>
          <Select>
            {(schema.status_sources || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="is_active" label="启用" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用"/>
        </Form.Item>
      </Form>
    </Modal>
  );
}


function EdgeForm({visible, record, schema, nodes, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible) return;
    form.setFieldsValue({
      id: record && record.id,
      source: record && record.source,
      target: record && record.target,
      type: record && record.type ? record.type : 'depends_on',
      label: record && record.label,
      status: record && record.status ? record.status : 'unknown',
      status_source: record && record.status_source ? record.status_source : 'manual',
      probed: record ? record.probed : false,
      is_active: record ? record.is_active : true,
    });
  }, [visible, record, form]);

  function submit() {
    form.validateFields().then(values => {
      setSaving(true);
      return http.post('/api/v1/topology/edges/', {
        ...values,
        metadata: {},
      });
    }).then(data => {
      message.success('拓扑连线已保存');
      onSuccess(data);
    }).finally(() => setSaving(false));
  }

  return (
    <Modal
      visible={visible}
      width={720}
      title={record && record.id ? '编辑拓扑连线' : '新增拓扑连线'}
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Form form={form} labelCol={{span: 6}} wrapperCol={{span: 16}}>
        <Form.Item name="id" hidden><Input/></Form.Item>
        <Form.Item name="source" label="源节点" rules={[{required: true, message: '请选择源节点'}]}>
          <Select showSearch optionFilterProp="children">
            {nodes.map(item => (
              <Select.Option key={item.id} value={item.id}>{item.name}（{item.key}）</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="target" label="目标节点" rules={[{required: true, message: '请选择目标节点'}]}>
          <Select showSearch optionFilterProp="children">
            {nodes.map(item => (
              <Select.Option key={item.id} value={item.id}>{item.name}（{item.key}）</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="type" label="关系类型" rules={[{required: true}]}>
          <Select>
            {(schema.edge_types || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="label" label="标签" rules={[{max: 100}]}>
          <Input placeholder="可选，用于覆盖默认关系名称"/>
        </Form.Item>
        <Form.Item name="status" label="状态" rules={[{required: true}]}>
          <Select>
            {(schema.statuses || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="status_source" label="状态来源" rules={[{required: true}]}>
          <Select>
            {(schema.status_sources || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="probed" label="连接探测" valuePropName="checked">
          <Switch checkedChildren="已探测" unCheckedChildren="未探测"/>
        </Form.Item>
        <Form.Item name="is_active" label="启用" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用"/>
        </Form.Item>
      </Form>
    </Modal>
  );
}


export default function TopologyIndex() {
  const [diagnosisForm] = Form.useForm();
  const [scanForm] = Form.useForm();
  const [schema, setSchema] = useState({node_types: [], edge_types: [], statuses: [], status_sources: [], source_types: []});
  const [sources, setSources] = useState({hosts: [], applications: [], services: [], metric_targets: []});
  const [graphData, setGraphData] = useState({nodes: [], edges: []});
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('all');
  const [nodeRecord, setNodeRecord] = useState(null);
  const [edgeRecord, setEdgeRecord] = useState(null);
  const [diagnosisVisible, setDiagnosisVisible] = useState(false);
  const [diagnosisRunning, setDiagnosisRunning] = useState(false);
  const [diagnosisResult, setDiagnosisResult] = useState(null);
  const [scanVisible, setScanVisible] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState([]);
  const [viewMode, setViewMode] = useState('servers');
  const [dragPositions, setDragPositions] = useState({});
  const [draggingId, setDraggingId] = useState(null);
  const [zoom, setZoom] = useState(0.6);
  const dragState = useRef(null);
  const suppressClick = useRef(false);
  const zoomRef = useRef(0.6);
  const nodeTypes = useMemo(() => enumMap(schema.node_types), [schema]);
  const edgeTypes = useMemo(() => enumMap(schema.edge_types), [schema]);
  const statusTypes = useMemo(() => enumMap(schema.statuses), [schema]);
  const selectedHostId = useMemo(() => {
    const currentHostId = getRuntimeHostId(selected);
    if (currentHostId) return currentHostId;
    const firstHost = (graphData.nodes || []).find(item => (
      item.is_active && item.type === 'host' && item.source && item.source.type === 'host'
    ));
    return firstHost && firstHost.source ? firstHost.source.id : null;
  }, [selected, graphData]);
  const baseGraph = useMemo(
    () => buildLayout(graphData.nodes || [], graphData.edges || [], filter, viewMode, selectedHostId),
    [graphData, filter, viewMode, selectedHostId],
  );
  const graph = useMemo(() => applyDragPositions(baseGraph, dragPositions), [baseGraph, dragPositions]);
  const graphSummary = useMemo(() => {
    const allNodes = (graphData.nodes || []).filter(item => item.is_active);
    const runtimeNodes = allNodes.filter(item => item.source && item.source.type === 'runtime');
    return {
      visibleNodes: graph.nodes.length,
      visibleEdges: graph.edges.length,
      hosts: allNodes.filter(item => item.type === 'host').length,
      runtime: runtimeNodes.length,
    };
  }, [graph, graphData]);

  function reload() {
    setLoading(true);
    return Promise.all([
      http.get('/api/v1/topology/schema/').then(setSchema),
      http.get('/api/v1/topology/sources/').then(setSources),
      http.get('/api/v1/topology/graph/').then(setGraphData),
      http.get('/api/v1/topology/nodes/').then(setNodes),
      http.get('/api/v1/topology/edges/').then(setEdges),
    ]).finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    setDragPositions({});
  }, [filter, viewMode, selectedHostId]);

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  useEffect(() => {
    function handleMouseMove(event) {
      const drag = dragState.current;
      if (!drag) return;
      const scale = zoomRef.current || 1;
      const dx = (event.clientX - drag.startX) / scale;
      const dy = (event.clientY - drag.startY) / scale;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) suppressClick.current = true;
      setDragPositions(prev => ({
        ...prev,
        [drag.id]: {
          x: Math.max(12, drag.x + dx),
          y: Math.max(48, drag.y + dy),
        },
      }));
    }

    function handleMouseUp() {
      dragState.current = null;
      setDraggingId(null);
    }

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  useEffect(() => {
    if (!selected && graph.nodes.length) setSelected(graph.nodes[0]);
    if (selected && !graph.nodeMap[selected.id]) setSelected(graph.nodes[0] || null);
  }, [graph, selected]);

  function updateZoom(nextZoom) {
    const sorted = ZOOM_OPTIONS.slice().sort((a, b) => a - b);
    const minZoom = sorted[0];
    const maxZoom = sorted[sorted.length - 1];
    setZoom(Math.max(minZoom, Math.min(maxZoom, nextZoom)));
  }

  function stepZoom(direction) {
    const sorted = ZOOM_OPTIONS.slice().sort((a, b) => a - b);
    let index = sorted.findIndex(item => item >= zoom);
    if (index < 0) index = sorted.length - 1;
    updateZoom(sorted[Math.max(0, Math.min(sorted.length - 1, index + direction))]);
  }

  function startNodeDrag(node, event) {
    if (event.button !== 0) return;
    event.preventDefault();
    dragState.current = {
      id: node.id,
      startX: event.clientX,
      startY: event.clientY,
      x: node.x,
      y: node.y,
    };
    suppressClick.current = false;
    setDraggingId(node.id);
  }

  function selectNode(node) {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    setSelected(node);
    if (viewMode === 'servers' && node.type === 'host') {
      setViewMode('runtime');
    }
  }

  function removeNode(record) {
    Modal.confirm({
      title: '确定删除该拓扑节点？',
      content: '删除节点会同时删除与它关联的拓扑连线。',
      onOk: () => http.delete('/api/v1/topology/nodes/', {params: {id: record.id}}).then(reload),
    });
  }

  function removeEdge(record) {
    Modal.confirm({
      title: '确定删除该拓扑连线？',
      onOk: () => http.delete('/api/v1/topology/edges/', {params: {id: record.id}}).then(reload),
    });
  }

  function openDiagnosis() {
    if (!selected) return;
    diagnosisForm.setFieldsValue({
      question: `请结合拓扑关系、监控指标、告警和运行态证据，分析 ${selected.name} 当前的影响范围、可能故障点和下一步人工检查建议。`,
    });
    setDiagnosisResult(null);
    setDiagnosisVisible(true);
  }

  function runDiagnosis() {
    if (!selected) return;
    diagnosisForm.validateFields().then(values => {
      setDiagnosisRunning(true);
      return http.post('/api/v1/aiops/investigations/', {
        question: values.question.trim(),
        topology_node_ids: [selected.id],
        topology_radius: 2,
      }, {timeout: 125000});
    }).then(data => {
      setDiagnosisResult(data);
      if (data.status === 'completed') {
        message.success('AI 拓扑诊断已完成');
      } else {
        message.error(data.error || 'AI 拓扑诊断失败');
      }
    }).finally(() => setDiagnosisRunning(false));
  }

  function openRuntimeScan() {
    const hostId = selected && selected.source && selected.source.type === 'host' ? selected.source.id : undefined;
    scanForm.setFieldsValue({host_ids: hostId ? [Number(hostId)] : []});
    setScanResult([]);
    setScanVisible(true);
  }

  function runRuntimeScan() {
    let scanValues = {};
    scanForm.validateFields().then(values => {
      scanValues = values;
      setScanning(true);
      return http.post('/api/v1/topology/runtime-scan/', {
        host_ids: values.host_ids || [],
        dry_run: true,
      }, {timeout: 125000});
    }).then(data => {
      setScanResult(data || []);
      const failed = (data || []).filter(item => item.status === 'failed').length;
      if (failed) {
        message.warning(`运行态扫描完成，${failed} 台主机失败`);
      } else {
        message.success('运行态扫描完成');
      }
      if (!failed) {
        const services = [];
        const connections = [];
        (data || []).forEach(item => {
          const recommendations = item.recommendations || {};
          services.push(...(recommendations.services || []));
          connections.push(...(recommendations.connections || []));
        });
        if (services.length || connections.length) {
          let selectedServiceKeys = services.filter(item => item.selected !== false).map(item => item.key);
          let selectedConnectionKeys = connections.filter(item => item.selected !== false).map(item => item.key);
          Modal.confirm({
            width: 860,
            title: '确认固定拓扑监控项',
            okText: '确认加入监控',
            cancelText: '暂不加入',
            content: (
              <RuntimeDiscoveryPreview
                results={data || []}
                onSelectionChange={(serviceKeys, connectionKeys) => {
                  selectedServiceKeys = serviceKeys;
                  selectedConnectionKeys = connectionKeys;
                }}/>
            ),
            onOk: () => http.post('/api/v1/topology/runtime-scan/', {
              host_ids: scanValues.host_ids || [],
              selected_service_keys: selectedServiceKeys,
              selected_connection_keys: selectedConnectionKeys,
            }, {timeout: 125000}).then(result => {
              setScanResult(result || []);
              message.success('固定拓扑监控项已更新');
              reload();
            }),
          });
          return null;
        }
        return http.post('/api/v1/topology/runtime-scan/', {
          host_ids: scanValues.host_ids || [],
        }, {timeout: 125000}).then(result => {
          setScanResult(result || []);
          reload();
        });
      }
      reload();
    }).finally(() => setScanning(false));
  }

  const nodeMap = {};
  nodes.forEach(item => {
    nodeMap[item.id] = item;
  });

  return (
    <AuthDiv auth="topology.topology.view|topology.topology.manage">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>拓扑诊断</Breadcrumb.Item>
        <Breadcrumb.Item>拓扑图谱</Breadcrumb.Item>
      </Breadcrumb>
      <div className={styles.toolbar}>
        <Space>
          <Button icon={<ReloadOutlined/>} loading={loading} onClick={reload}>刷新</Button>
          <Radio.Group value={viewMode} onChange={event => setViewMode(event.target.value)} buttonStyle="solid">
            <Radio.Button value="servers">服务器链路</Radio.Button>
            <Radio.Button value="runtime">服务链路</Radio.Button>
            <Radio.Button value="full">完整视图</Radio.Button>
          </Radio.Group>
          <Select value={filter} onChange={setFilter} style={{width: 136}}>
            <Select.Option value="all">全部状态</Select.Option>
            {(schema.statuses || []).map(item => (
              <Select.Option key={item.key} value={item.key}>{item.name}</Select.Option>
            ))}
          </Select>
          <span className={styles.graphCounter}>
            {graphSummary.visibleNodes} 节点 / {graphSummary.visibleEdges} 连线
          </span>
          <Button.Group>
            <Tooltip title="缩小拓扑">
              <Button icon={<ZoomOutOutlined/>} onClick={() => stepZoom(-1)}/>
            </Tooltip>
            <Select
              value={zoom}
              onChange={updateZoom}
              className={styles.zoomSelect}>
              {ZOOM_OPTIONS.map(item => (
                <Select.Option key={item} value={item}>{Math.round(item * 100)}%</Select.Option>
              ))}
            </Select>
            <Tooltip title="放大拓扑">
              <Button icon={<ZoomInOutlined/>} onClick={() => stepZoom(1)}/>
            </Tooltip>
          </Button.Group>
        </Space>
        <Space>
          <AuthButton auth="topology.topology.manage" icon={<SearchOutlined/>} onClick={openRuntimeScan}>
            扫描运行态
          </AuthButton>
          <AuthButton auth="topology.topology.manage" icon={<PlusOutlined/>} onClick={() => setNodeRecord({})}>
            新增节点
          </AuthButton>
          <AuthButton auth="topology.topology.manage" type="primary" icon={<LinkOutlined/>} onClick={() => setEdgeRecord({})}>
            新增连线
          </AuthButton>
        </Space>
      </div>
      {graphData.summary_error && (
        <Alert showIcon type="warning" message={graphData.summary_error} style={{marginTop: 16}}/>
      )}
      <div className={styles.workspace}>
        <div className={styles.canvasWrap}>
          {graph.nodes.length ? (
            <div
              className={styles.zoomSurface}
              style={{width: graph.width * zoom, height: graph.height * zoom}}>
              <div
                className={styles.canvas}
                style={{
                  width: graph.width,
                  height: graph.height,
                  transform: `scale(${zoom})`,
                }}>
                {Object.entries(graph.columns).map(([type, col]) => (
                  <div key={type} className={styles.columnTitle} style={{left: col.x}}>
                    {nodeTypes[type] || type}
                  </div>
                ))}
                <svg className={styles.edges} viewBox={`0 0 ${graph.width} ${graph.height}`}>
                  <defs>
                    <marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                      <path d="M0,0 L8,4 L0,8 Z" className={styles.arrow}/>
                    </marker>
                  </defs>
                  {graph.edges.map(edge => (
                    <Edge
                      key={edge.id}
                      edge={edge}
                      source={graph.nodeMap[edge.source]}
                      target={graph.nodeMap[edge.target]}
                      edgeTypes={edgeTypes}
                      focused={selected && (selected.id === edge.source || selected.id === edge.target)}/>
                  ))}
                </svg>
                {graph.nodes.map(node => (
                  <Node
                    key={node.id}
                    node={node}
                    selected={selected && selected.id === node.id}
                    dragging={draggingId === node.id}
                    onSelect={selectNode}
                    onDragStart={startNodeDrag}/>
                ))}
              </div>
            </div>
          ) : (
            <Empty description="暂无可展示的拓扑数据"/>
          )}
        </div>
        <div className={styles.detail}>
          {selected ? (
            <React.Fragment>
              <div className={styles.detailTitle}>
                <span>{selected.name}</span>
                <Tag color={(statusMeta[selected.status] || statusMeta.unknown).color}>
                  {statusTypes[selected.status] || selected.status}
                </Tag>
              </div>
              <div className={styles.detailMeta}>{nodeTypes[selected.type] || selected.type} · {selected.key}</div>
              <div className={styles.sectionTitle}>来源</div>
              <div className={styles.detailMeta}>
                {(sourceTypeLabel[selected.source.type] || selected.source.type)} · {selected.source.id || '无来源对象'}
              </div>
              <div className={styles.sectionTitle}>描述</div>
              <div className={styles.detailMeta}>{selected.description || '无'}</div>
              <div className={styles.sectionTitle}>监控与告警</div>
              <ObservabilityDetail node={selected}/>
              <div className={styles.sectionTitle}>运行态</div>
              <RuntimeDetail node={selected}/>
              <div className={styles.sectionTitle}>连接</div>
              <ConnectedList node={selected} graph={graph} edgeTypes={edgeTypes}/>
              {hasPermission('aiops.investigation.run') && (
                <Button
                  block
                  type="primary"
                  icon={<RobotOutlined/>}
                  style={{marginTop: 18}}
                  onClick={openDiagnosis}>
                  AI 诊断
                </Button>
              )}
            </React.Fragment>
          ) : <Empty description="请选择节点"/>}
        </div>
      </div>
      <div className={styles.tables}>
        <Tabs defaultActiveKey="nodes">
          <Tabs.TabPane tab="节点管理" key="nodes">
            <Table rowKey="id" dataSource={nodes} loading={loading} pagination={{showSizeChanger: true}}>
              <Table.Column title="名称" dataIndex="name"/>
              <Table.Column title="类型" dataIndex="type" render={value => nodeTypes[value] || value}/>
              <Table.Column title="标识" dataIndex="key"/>
              <Table.Column title="来源" render={item => sourceTypeLabel[item.source.type] || item.source.type}/>
              <Table.Column title="状态" dataIndex="status" render={value => (
                <Tag color={(statusMeta[value] || statusMeta.unknown).color}>{statusTypes[value] || value}</Tag>
              )}/>
              <Table.Column title="启用" dataIndex="is_active" render={value => value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>}/>
              <Table.Column title="操作" width={150} render={record => (
                <Action>
                  <Action.Button auth="topology.topology.manage" onClick={() => setNodeRecord(record)}>编辑</Action.Button>
                  <Action.Button auth="topology.topology.manage" danger onClick={() => removeNode(record)}>删除</Action.Button>
                </Action>
              )}/>
            </Table>
          </Tabs.TabPane>
          <Tabs.TabPane tab="连线管理" key="edges">
            <Table rowKey="id" dataSource={edges} loading={loading} pagination={{showSizeChanger: true}}>
              <Table.Column title="源节点" dataIndex="source" render={value => nodeMap[value] ? nodeMap[value].name : value}/>
              <Table.Column title="目标节点" dataIndex="target" render={value => nodeMap[value] ? nodeMap[value].name : value}/>
              <Table.Column title="关系" dataIndex="type" render={value => edgeTypes[value] || value}/>
              <Table.Column title="标签" dataIndex="label"/>
              <Table.Column title="状态" dataIndex="status" render={value => (
                <Tag color={(statusMeta[value] || statusMeta.unknown).color}>{statusTypes[value] || value}</Tag>
              )}/>
              <Table.Column title="探测" dataIndex="probed" render={value => value ? <Tag color="blue">已探测</Tag> : <Tag>未探测</Tag>}/>
              <Table.Column title="启用" dataIndex="is_active" render={value => value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>}/>
              <Table.Column title="操作" width={150} render={record => (
                <Action>
                  <Action.Button auth="topology.topology.manage" onClick={() => setEdgeRecord(record)}>编辑</Action.Button>
                  <Action.Button auth="topology.topology.manage" danger onClick={() => removeEdge(record)}>删除</Action.Button>
                </Action>
              )}/>
            </Table>
          </Tabs.TabPane>
        </Tabs>
      </div>
      {nodeRecord !== null && (
        <NodeForm
          visible={nodeRecord !== null}
          record={nodeRecord}
          schema={schema}
          sources={sources}
          onCancel={() => setNodeRecord(null)}
          onSuccess={() => {
            setNodeRecord(null);
            reload();
          }}/>
      )}
      {edgeRecord !== null && (
        <EdgeForm
          visible={edgeRecord !== null}
          record={edgeRecord}
          schema={schema}
          nodes={nodes}
          onCancel={() => setEdgeRecord(null)}
          onSuccess={() => {
            setEdgeRecord(null);
            reload();
          }}/>
      )}
      <Modal
        visible={scanVisible}
        width={760}
        title={<Space><SearchOutlined/>扫描运行态拓扑</Space>}
        confirmLoading={scanning}
        okText="开始扫描"
        onOk={runRuntimeScan}
        onCancel={() => setScanVisible(false)}
        destroyOnClose>
        <Alert
          showIcon
          type="warning"
          message="扫描只执行固定只读采集，保存进程名、PID、监听端口和 TCP 已建立连接，不采集命令行参数、输出或密钥。"
          style={{marginBottom: 16}}/>
        <Form form={scanForm} layout="vertical" preserve={false}>
          <Form.Item name="host_ids" label="主机范围" rules={[{required: true, message: '请选择主机'}]}>
            <Select mode="multiple" showSearch optionFilterProp="children" maxTagCount={4}>
              {(sources.hosts || []).map(item => (
                <Select.Option key={item.id} value={item.id}>
                  {item.name}（{item.hostname}）
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
        {scanResult.length > 0 && (
          <Table
            size="small"
            rowKey="host_id"
            dataSource={scanResult}
            pagination={false}
            style={{marginTop: 16}}>
            <Table.Column title="主机" dataIndex="host_name"/>
            <Table.Column title="状态" dataIndex="status" render={value => (
              <Tag color={value === 'succeeded' ? 'green' : 'red'}>{value === 'succeeded' ? '成功' : '失败'}</Tag>
            )}/>
            <Table.Column title="进程" dataIndex="process_count"/>
            <Table.Column title="监听" dataIndex="listener_count"/>
            <Table.Column title="连接" dataIndex="connection_count"/>
            <Table.Column title="业务连接" dataIndex="business_connection_count"/>
            <Table.Column title="错误" dataIndex="error"/>
          </Table>
        )}
      </Modal>
      <Modal
        visible={diagnosisVisible}
        width={760}
        title={<Space><SearchOutlined/>AI 拓扑诊断</Space>}
        confirmLoading={diagnosisRunning}
        okText="开始诊断"
        onOk={runDiagnosis}
        onCancel={() => setDiagnosisVisible(false)}
        destroyOnClose>
        <Form form={diagnosisForm} layout="vertical" preserve={false}>
          <Form.Item
            name="question"
            label="诊断问题"
            rules={[
              {required: true, message: '请输入诊断问题'},
              {min: 10, max: 4000, message: '长度必须在 10 到 4000 个字符之间'},
            ]}>
            <Input.TextArea rows={5} maxLength={4000} showCount/>
          </Form.Item>
        </Form>
      </Modal>
      <TopologyDiagnosisDrawer
        visible={Boolean(diagnosisResult)}
        record={diagnosisResult}
        onClose={() => setDiagnosisResult(null)}/>
    </AuthDiv>
  );
}
