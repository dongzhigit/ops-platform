import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Select, Space, Statistic, Tag, Tooltip } from 'antd';
import {
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DeploymentUnitOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { http } from 'libs';
import styles from './TopologyPanel.module.less';


const statusMeta = {
  healthy: {label: '正常', color: 'green'},
  info: {label: '提示', color: 'blue'},
  warning: {label: '告警', color: 'orange'},
  critical: {label: '严重', color: 'red'},
  offline: {label: '离线', color: 'red'},
  disabled: {label: '停用', color: 'default'},
  unknown: {label: '未知', color: 'default'},
};

const edgeTypeText = {
  deployed_on: '部署',
  depends_on_app: '应用依赖',
  depends_on_service: '服务依赖',
};

const nodeTypeText = {
  service: '服务',
  app: '应用',
  host: '服务器',
};

const nodeIcon = {
  service: <ApiOutlined/>,
  app: <AppstoreOutlined/>,
  host: <CloudServerOutlined/>,
};
const NODE_WIDTH = 300;
const NODE_HEIGHT = 92;
const ROW_SPACING = 164;
const EDGE_LABEL_CHARS = 18;
const EDGE_LABEL_LINE_HEIGHT = 16;


function nodeStatusMeta(node) {
  if (node.type !== 'host' && node.status === 'critical') {
    return {label: '受影响', color: 'red'};
  }
  return statusMeta[node.status] || statusMeta.unknown;
}


function asFixed(value, suffix = '') {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '--';
  return `${Number(value).toFixed(1)}${suffix}`;
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


function buildLayout(nodes, edges, filter) {
  const columns = {
    service: {x: 42, y: 56, items: []},
    app: {x: 422, y: 56, items: []},
    host: {x: 802, y: 56, items: []},
  };
  nodes.forEach(node => {
    if (columns[node.type]) columns[node.type].items.push(node);
  });
  Object.values(columns).forEach(col => {
    col.items.sort((a, b) => {
      const left = a.status === 'healthy' ? 1 : 0;
      const right = b.status === 'healthy' ? 1 : 0;
      return left - right || a.name.localeCompare(b.name);
    });
  });

  let visibleIds = new Set(nodes.map(item => item.id));
  if (filter === 'abnormal') {
    visibleIds = new Set(nodes.filter(item => item.status !== 'healthy').map(item => item.id));
    edges.forEach(edge => {
      if (visibleIds.has(edge.source) || visibleIds.has(edge.target)) {
        visibleIds.add(edge.source);
        visibleIds.add(edge.target);
      }
    });
  } else if (filter === 'unprobed') {
    visibleIds = new Set();
    edges.filter(edge => !edge.probed).forEach(edge => {
      visibleIds.add(edge.source);
      visibleIds.add(edge.target);
    });
  }

  const positioned = [];
  Object.entries(columns).forEach(([type, col]) => {
    col.items.filter(item => visibleIds.has(item.id)).forEach((item, index) => {
      positioned.push({
        ...item,
        x: col.x,
        y: col.y + index * ROW_SPACING,
      });
    });
  });
  const nodeMap = {};
  positioned.forEach(item => {
    nodeMap[item.id] = item;
  });
  const visibleEdges = edges.filter(edge => nodeMap[edge.source] && nodeMap[edge.target]);
  const height = Math.max(420, 120 + Math.max(
    columns.service.items.filter(item => visibleIds.has(item.id)).length,
    columns.app.items.filter(item => visibleIds.has(item.id)).length,
    columns.host.items.filter(item => visibleIds.has(item.id)).length,
    1,
  ) * ROW_SPACING);
  return {nodes: positioned, nodeMap, edges: visibleEdges, width: 1144, height};
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
  return {...layout, nodes: nextNodes, nodeMap, width, height};
}


function Node({node, selected, dragging, onSelect, onDragStart}) {
  const meta = nodeStatusMeta(node);
  const detail = [node.name, node.key || node.description || nodeTypeText[node.type]].filter(Boolean).join('\n');
  return (
    <button
      type="button"
      className={`${styles.node} ${styles[node.type]} ${styles[node.status]} ${selected ? styles.selected : ''} ${dragging ? styles.dragging : ''}`}
      style={{left: node.x, top: node.y}}
      title={detail}
      onMouseDown={event => onDragStart(node, event)}
      onClick={() => onSelect(node)}>
      <span className={styles.nodeIcon}>{nodeIcon[node.type] || <DeploymentUnitOutlined/>}</span>
      <span className={styles.nodeBody}>
        <span className={styles.nodeTitle}>{node.name}</span>
        <span className={styles.nodeMeta}>{node.key || node.description || nodeTypeText[node.type]}</span>
      </span>
      <Tag color={meta.color} className={styles.nodeStatus}>{meta.label}</Tag>
    </button>
  );
}


function Edge({edge, source, target}) {
  const forward = source.x <= target.x;
  const x1 = source.x + (forward ? NODE_WIDTH : 0);
  const y1 = source.y + 46;
  const x2 = target.x + (forward ? 0 : NODE_WIDTH);
  const y2 = target.y + 46;
  const mid = (x1 + x2) / 2;
  const label = edge.label || edgeTypeText[edge.type] || '连接';
  const labelLines = splitEdgeLabel(label);
  const labelX = (x1 + x2) / 2;
  const labelHeight = labelLines.length * EDGE_LABEL_LINE_HEIGHT + 8;
  const labelWidth = Math.max(72, Math.max(...labelLines.map(labelTextWidth)) + 16);
  const labelTop = (y1 + y2) / 2 - labelHeight / 2;
  return (
    <g>
      <path
        className={`${styles.edge} ${styles[edge.status || 'unknown']} ${edge.probed ? '' : styles.dashed}`}
        d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
        markerEnd="url(#arrow)"/>
      <g>
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


function edgeEndpoints(edge, nodeMap) {
  const source = nodeMap[edge.source];
  const target = nodeMap[edge.target];
  if (!source || !target) return {};
  if (edge.type === 'depends_on_service') {
    return {source: target, target: source};
  }
  return {source, target};
}


function calcStats(nodes, edges) {
  return {
    hosts: nodes.filter(item => item.type === 'host').length,
    apps: nodes.filter(item => item.type === 'app').length,
    services: nodes.filter(item => item.type === 'service').length,
    abnormal: nodes.filter(item => item.status !== 'healthy').length,
    unprobed: edges.filter(item => !item.probed).length,
  };
}


function ConnectedList({node, graph}) {
  const related = graph.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  if (!related.length) return <span className={styles.emptyText}>暂无可见连接</span>;
  return related.slice(0, 12).map(edge => {
    const otherId = edge.source === node.id ? edge.target : edge.source;
    const other = graph.nodeMap[otherId];
    if (!other) return null;
    const meta = statusMeta[edge.status] || statusMeta.unknown;
    return (
      <div key={edge.id} className={styles.connectionRow}>
        <span>{edgeTypeText[edge.type] || '连接'}</span>
        <b>{other.name}</b>
        <Tag color={meta.color}>{edge.probed ? '实测' : '未探测'}</Tag>
      </div>
    );
  });
}


export default function TopologyPanel() {
  const [data, setData] = useState({nodes: [], edges: []});
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [dragPositions, setDragPositions] = useState({});
  const [draggingId, setDraggingId] = useState(null);
  const dragState = useRef(null);
  const suppressClick = useRef(false);

  function reload() {
    setLoading(true);
    return http.get('/api/v1/observability/topology/')
      .then(setData)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
  }, []);

  const baseGraph = useMemo(() => buildLayout(data.nodes || [], data.edges || [], filter), [data, filter]);
  const graph = useMemo(() => applyDragPositions(baseGraph, dragPositions), [baseGraph, dragPositions]);
  const stats = useMemo(() => calcStats(data.nodes || [], data.edges || []), [data]);

  useEffect(() => {
    setDragPositions({});
  }, [filter]);

  useEffect(() => {
    function handleMouseMove(event) {
      const drag = dragState.current;
      if (!drag) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
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
  }

  return (
    <Space direction="vertical" size="large" style={{width: '100%'}}>
      <Alert
        showIcon
        type={data.summary_error ? 'warning' : 'info'}
        message={data.summary_error || '业务架构图基于应用依赖、服务依赖、部署目标、主机指标和告警生成。虚线表示当前没有真实连接探测结果。'}/>
      <div className={styles.toolbar}>
        <Space>
          <Button icon={<ReloadOutlined/>} loading={loading} onClick={reload}>刷新</Button>
          <Select value={filter} onChange={setFilter} style={{width: 156}}>
            <Select.Option value="all">全部关系</Select.Option>
            <Select.Option value="abnormal">只看异常</Select.Option>
            <Select.Option value="unprobed">未探测连接</Select.Option>
          </Select>
          <Button type="primary" onClick={() => window.open('/topology', '_blank')}>
            打开分层拓扑
          </Button>
        </Space>
        <Space size="large" className={styles.stats}>
          <Statistic title="服务器" value={stats.hosts}/>
          <Statistic title="应用" value={stats.apps}/>
          <Statistic title="服务" value={stats.services}/>
          <Statistic title="异常节点" value={stats.abnormal}/>
          <Statistic title="未探测连接" value={stats.unprobed}/>
        </Space>
      </div>
      <div className={styles.workspace}>
        <div className={styles.canvasWrap}>
          {graph.nodes.length ? (
              <div className={styles.canvas} style={{height: graph.height, width: graph.width}}>
                <div className={styles.columnTitle} style={{left: 42}}>服务</div>
              <div className={styles.columnTitle} style={{left: 422}}>应用</div>
              <div className={styles.columnTitle} style={{left: 802}}>服务器</div>
              <svg className={styles.edges} viewBox={`0 0 ${graph.width} ${graph.height}`}>
                <defs>
                  <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                    <path d="M0,0 L8,4 L0,8 Z" className={styles.arrow}/>
                  </marker>
                </defs>
                {graph.edges.map(edge => {
                  const endpoints = edgeEndpoints(edge, graph.nodeMap);
                  return (
                    <Edge key={edge.id} edge={edge} source={endpoints.source} target={endpoints.target}/>
                  );
                })}
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
          ) : (
            <Empty description="暂无可展示的业务拓扑数据"/>
          )}
        </div>
        <div className={styles.detail}>
          {selected ? (
            <React.Fragment>
              <div className={styles.detailTitle}>
                <span>{selected.name}</span>
                <Tag color={nodeStatusMeta(selected).color}>
                  {nodeStatusMeta(selected).label}
                </Tag>
              </div>
              <div className={styles.detailMeta}>{nodeTypeText[selected.type]} · {selected.key || selected.description || '--'}</div>
              {selected.type === 'host' && (
                <React.Fragment>
                  <div className={styles.metricGrid}>
                    <Statistic title="CPU" value={asFixed(selected.metrics && selected.metrics.cpu, '%')}/>
                    <Statistic title="内存" value={asFixed(selected.metrics && selected.metrics.memory, '%')}/>
                    <Statistic title="磁盘" value={asFixed(selected.metrics && selected.metrics.disk, '%')}/>
                    <Statistic title="网络" value={asFixed(selected.metrics && selected.metrics.network)}/>
                  </div>
                  <div className={styles.sectionTitle}>告警</div>
                  {selected.alerts && selected.alerts.length ? selected.alerts.slice(0, 5).map(item => (
                    <Tooltip key={item.id} title={item.summary || item.description}>
                      <Tag color={(statusMeta[item.severity] || statusMeta.warning).color}>{item.alert_name}</Tag>
                    </Tooltip>
                  )) : <span className={styles.emptyText}>当前无触发告警</span>}
                </React.Fragment>
              )}
              {selected.type === 'app' && (
                <div className={styles.hints}>
                  <p>发布配置数量：{selected.deploy_count || 0}</p>
                  <p>{selected.description || '无备注'}</p>
                </div>
              )}
              {selected.type === 'service' && (
                <div className={styles.hints}>
                  <p>{selected.description || '配置中心服务，当前没有独立连接探测结果。'}</p>
                </div>
              )}
              <div className={styles.sectionTitle}>连接</div>
              <ConnectedList node={selected} graph={graph}/>
            </React.Fragment>
          ) : null}
        </div>
      </div>
    </Space>
  );
}
