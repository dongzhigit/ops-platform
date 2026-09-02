/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import styles from './index.module.css';
import { Descriptions, Spin, Alert } from 'antd';
import { observer } from 'mobx-react'
import { http, VERSION } from 'libs';


@observer
class About extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      fetching: true,
      info: {}
    }
  }

  componentDidMount() {
    http.get('/api/setting/about/')
      .then(res => this.setState({info: res}))
      .finally(() => this.setState({fetching: false}))
  }


  render() {
    const {info, fetching} = this.state;
    return (
      <Spin spinning={fetching}>
        <div className={styles.title}>关于</div>
        <Descriptions column={1}>
          <Descriptions.Item label="操作系统">{info['system_version']}</Descriptions.Item>
          <Descriptions.Item label="Python版本">{info['python_version']}</Descriptions.Item>
          <Descriptions.Item label="Django版本">{info['django_version']}</Descriptions.Item>
          <Descriptions.Item label="运维平台版本">{info['ops_platform_version']}</Descriptions.Item>
          <Descriptions.Item label="ops-platform API版本">{info['api_version']}</Descriptions.Item>
          <Descriptions.Item label="ops-platform Web版本">{VERSION}</Descriptions.Item>
        </Descriptions>
        {info['api_version'] !== VERSION && (
          <Alert showIcon style={{width: 500}} type="warning" message="ops-platform API版本与Web版本不匹配，请尝试刷新浏览器后再次查看。"/>
        )}
      </Spin>
    )
  }
}

export default About
