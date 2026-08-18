import React, { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Modal, Radio, Row, Select,
  Space, Statistic, Table, Tag, message,
} from 'antd';
import { Chart, Geom, Axis, Tooltip } from 'bizcharts';
import { EditOutlined, PlusOutlined } from '@ant-design/icons';
import moment from 'moment';
import { hasPermission, http } from 'libs';
import MetricTargetForm from './MetricTargetForm';


const metricInfo = {
  cpu: ['CPU 使用率', '%'],
  memory: ['内存使用率', '%'],
  disk: ['磁盘最高使用率', '%'],
  network: ['网络吞吐', 'B/s'],
};


function formatPercent(value) {
  return value === undefined ? '--' : `${Number(value).toFixed(1)}%`;
}


function formatBytes(value) {
  if (value === undefined) return '--';
  const number = Number(value);
  if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(1)} MiB/s`;
  if (number >= 1024) return `${(number / 1024).toFixed(1)} KiB/s`;
  return `${number.toFixed(0)} B/s`;
}


function MetricChart({metric, data, loading}) {
  const [title, unit] = metricInfo[metric];
  return (
    <Card size="small" title={title} loading={loading}>
      {data.length ? (
        <Chart
          height={230}
          forceFit
          data={data}
          padding={[10, 20, 40, 55]}
          scale={{value: {alias: unit}}}>
          <Axis name="time"/>
          <Axis name="value"/>
          <Tooltip crosshairs={{type: 'y'}}/>
          <Geom type="line" position="time*value" size={2}/>
        </Chart>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="所选时段暂无指标"/>}
    </Card>
  );
}


export default function MetricsPanel() {
  const [targets, setTargets] = useState([]);
  const [summary, setSummary] = useState([]);
  const [selectedHost, setSelectedHost] = useState();
  const [range, setRange] = useState(6);
  const [trend, setTrend] = useState({cpu: [], memory: [], disk: [], network: []});
  const [loading, setLoading] = useState(false);
  const [trendLoading, setTrendLoading] = useState(false);
  const [formRecord, setFormRecord] = useState(null);
  const [formVisible, setFormVisible] = useState(false);

  function reload() {
    setLoading(true);
    const targetsRequest = http.get('/api/v1/observability/targets/').then(targetData => {
      setTargets(targetData);
      if (targetData.length && !targetData.some(item => item.host.id === selectedHost)) {
        setSelectedHost(targetData[0].host.id);
      }
      if (!targetData.length) setSelectedHost(undefined);
    });
    const summaryRequest = http.get('/api/v1/observability/metrics/summary/')
      .then(setSummary)
      .catch(() => setSummary([]));
    return Promise.all([targetsRequest, summaryRequest]).finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedHost) return;
    const end = Math.floor(Date.now() / 1000);
    const start = end - range * 3600;
    const step = range <= 1 ? 30 : (range <= 6 ? 60 : 300);
    setTrendLoading(true);
    const metrics = Object.keys(metricInfo);
    http.all(metrics.map(metric => http.get('/api/v1/observability/metrics/query/', {
      params: {host_id: selectedHost, metric, start, end, step},
    }))).then(results => {
      const output = {};
      results.forEach((result, index) => {
        const row = result.result[0] || {};
        output[metrics[index]] = (row.values || []).map(point => ({
          time: moment.unix(Number(point[0])).format('MM-DD HH:mm'),
          value: Number(point[1]),
        }));
      });
      setTrend(output);
    }).finally(() => setTrendLoading(false));
  }, [selectedHost, range]);

  function edit(record = {}) {
    setFormRecord(record);
    setFormVisible(true);
  }

  function remove(record) {
    Modal.confirm({
      title: '删除采集目标',
      content: `确定停止并删除 ${record.host.name} 的指标采集配置？已有告警历史会保留。`,
      onOk: () => http.delete('/api/v1/observability/targets/', {params: {id: record.id}})
        .then(() => {
          message.success('采集目标已删除');
          reload();
        }),
    });
  }

  return (
    <Space direction="vertical" size="large" style={{width: '100%'}}>
      <Alert
        showIcon
        type="info"
        message="主机需要运行 node_exporter，Prometheus 和 Alertmanager 只通过内部网络访问本平台。所有指标与告警仍受主机对象授权过滤。"/>
      <Card
        title="主机指标总览"
        loading={loading}
        extra={<Button onClick={reload}>刷新</Button>}>
        {summary.length ? (
          <Row gutter={[16, 16]}>
            {summary.map(item => {
              const metrics = item.metrics;
              const online = String(metrics.availability) === '1';
              return (
                <Col xs={24} md={12} xl={8} key={item.target.id}>
                  <Card
                    size="small"
                    hoverable
                    onClick={() => setSelectedHost(item.target.host.id)}
                    title={item.target.host.name}
                    extra={<Tag color={online ? 'green' : 'red'}>{online ? '在线' : '离线/未知'}</Tag>}>
                    <Row gutter={12}>
                      <Col span={8}><Statistic title="CPU" value={formatPercent(metrics.cpu)}/></Col>
                      <Col span={8}><Statistic title="内存" value={formatPercent(metrics.memory)}/></Col>
                      <Col span={8}><Statistic title="磁盘" value={formatPercent(metrics.disk)}/></Col>
                    </Row>
                    <div style={{marginTop: 10, color: '#666'}}>网络：{formatBytes(metrics.network)}</div>
                  </Card>
                </Col>
              );
            })}
          </Row>
        ) : <Empty description="尚未配置可访问的指标采集目标"/>}
      </Card>

      <Card
        title="指标趋势"
        extra={(
          <Space>
            <Select
              style={{width: 220}}
              value={selectedHost}
              onChange={setSelectedHost}
              placeholder="选择主机">
              {targets.filter(item => item.is_active).map(item => (
                <Select.Option key={item.host.id} value={item.host.id}>{item.host.name}</Select.Option>
              ))}
            </Select>
            <Radio.Group value={range} onChange={event => setRange(event.target.value)}>
              <Radio.Button value={1}>1 小时</Radio.Button>
              <Radio.Button value={6}>6 小时</Radio.Button>
              <Radio.Button value={24}>24 小时</Radio.Button>
            </Radio.Group>
          </Space>
        )}>
        <Row gutter={[16, 16]}>
          {Object.keys(metricInfo).map(metric => (
            <Col xs={24} xl={12} key={metric}>
              <MetricChart metric={metric} data={trend[metric] || []} loading={trendLoading}/>
            </Col>
          ))}
        </Row>
      </Card>

      <Card
        title="采集目标"
        extra={hasPermission('monitor.metrics.manage') ? (
          <Button type="primary" icon={<PlusOutlined/>} onClick={() => edit()}>新增目标</Button>
        ) : null}>
        <Table rowKey="id" dataSource={targets} loading={loading} pagination={false}>
          <Table.Column title="主机" render={item => `${item.host.name}（${item.host.hostname}）`}/>
          <Table.Column title="Exporter" render={item => `${item.scheme}://${item.exporter_address}:${item.exporter_port}${item.metrics_path}`}/>
          <Table.Column title="状态" dataIndex="is_active" render={value => (
            <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag>
          )}/>
          <Table.Column title="通知方式" dataIndex="notify_mode" render={value => value.length ? value.join(', ') : '仅站内'}/>
          {hasPermission('monitor.metrics.manage') && (
            <Table.Column title="操作" width={140} render={item => (
              <Space>
                <Button type="link" icon={<EditOutlined/>} onClick={() => edit(item)}>编辑</Button>
                <Button type="link" danger onClick={() => remove(item)}>删除</Button>
              </Space>
            )}/>
          )}
        </Table>
      </Card>
      <MetricTargetForm
        visible={formVisible}
        record={formRecord}
        onCancel={() => setFormVisible(false)}
        onSuccess={() => {
          setFormVisible(false);
          reload();
        }}/>
    </Space>
  );
}
