/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Breadcrumb, Table, Switch, Progress, Modal, Input, message } from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  HomeOutlined,
  UploadOutlined,
  EditOutlined
} from '@ant-design/icons';
import { ApprovalGate, AuthButton, Action } from 'components';
import { http, uniqueId, X_TOKEN } from 'libs';
import lds from 'lodash';
import styles from './index.module.less'
import moment from 'moment';


class FileManager extends React.Component {
  constructor(props) {
    super(props);
    this.input = null;
    this.pwdHistoryCaches = new Map()
    this.state = {
      fetching: false,
      showDot: false,
      uploading: false,
      inputPath: null,
      uploadStatus: 'active',
      approvalRequest: null,
      pwd: [],
      objects: [],
      percent: 0
    }
  }

  componentDidMount() {
    this.fetchFiles()
  }

  componentDidUpdate(prevProps) {
    if (this.props.id !== prevProps.id) {
      let pwd = this.pwdHistoryCaches.get(this.props.id) || []
      this.setState({objects: [], pwd})
      this.fetchFiles(pwd)
    }
  }

  componentWillUnmount() {
    if (this.socket) this.socket.close()
  }

  columns = [{
    title: '名称',
    key: 'name',
    render: info => info.kind === 'd' ? (
      <div onClick={() => this.handleChdir(info.name, '1')} style={{cursor: 'pointer'}}>
        <FolderOutlined style={{color: info.is_link ? '#008b8b' : '#2563fc'}}/>
        <span style={{color: info.is_link ? '#008b8b' : '#2563fc', paddingLeft: 5}}>{info.name}</span>
      </div>
    ) : (
      <React.Fragment>
        <FileOutlined/>
        <span style={{paddingLeft: 5}}>{info.name}</span>
      </React.Fragment>
    ),
    ellipsis: true
  }, {
    title: '大小',
    dataIndex: 'size',
    align: 'right',
    className: styles.fileSize,
    width: 90
  }, {
    title: '修改时间',
    dataIndex: 'date',
    sorter: (a, b) => moment(a.date).unix() - moment(b.date).unix(),
    width: 190
  }, {
    title: '属性',
    dataIndex: 'code',
    width: 110
  }, {
    title: '操作',
    width: 100,
    align: 'right',
    key: 'action',
    render: info => info.kind === '-' ? (
      <Action>
        <Action.Button className={styles.drawerBtn} icon={<DownloadOutlined/>}
                       onClick={() => this.handleDownload(info.name)}/>
        <Action.Button danger auth="host.console.del" className={styles.drawerBtn} icon={<DeleteOutlined/>}
                       onClick={() => this.handleDelete(info.name)}/>
      </Action>
    ) : null
  }];

  _kindSort = (item) => {
    return item.kind === 'd'
  };

  fetchFiles = (pwd) => {
    this.setState({ fetching: true });
    pwd = pwd || this.state.pwd;
    const path = '/' + pwd.join('/');
    return http.get('/api/file/', {params: {id: this.props.id, path}})
      .then(res => {
        const objects = lds.orderBy(res, [this._kindSort, 'name'], ['desc', 'asc']);
        this.setState({objects, pwd})
        this.pwdHistoryCaches.set(this.props.id, pwd)
        this.state.inputPath !== null && this.setState({inputPath: path})
      })
      .finally(() => this.setState({fetching: false}))
  };

  handleChdir = (name, action) => {
    let pwd = this.state.pwd.map(x => x);
    if (action === '1') {
      pwd.push(name)
      this.setState({inputPath: null})
    } else if (action === '2') {
      const index = pwd.indexOf(name);
      pwd = pwd.splice(0, index + 1)
    } else {
      pwd = []
    }
    this.fetchFiles(pwd)
  };

  handleInputEdit = () => {
    let inputPath = '/' + this.state.pwd.join('/')
    this.setState({inputPath})
  }

  handleInputEnter = () => {
    if (this.state.inputPath) {
      let pwdStr = this.state.inputPath.replace(/^\/+/, '')
      pwdStr = pwdStr.replace(/\/+$/, '')
      this.fetchFiles(pwdStr.split('/'))
        .then(() => this.setState({inputPath: null}))
    } else {
      this.setState({inputPath: null})
    }
  }

  handleUpload = () => {
    this.input.onchange = e => {
      const file = e.target['files'][0];
      this.input.value = '';
      if (!file) return
      const hostId = Number(this.props.id);
      const path = '/' + this.state.pwd.join('/');
      const payload = {
        host_ids: [hostId],
        path,
        filename: file.name,
        size: file.size,
      }
      this.setState({
        approvalRequest: {
          operation: {
            action: 'file.write',
            resource_type: 'host',
            resource_ids: [hostId],
            payload,
          },
          file,
          hostId,
          path,
          summary: `向主机 ${hostId} 的 ${path} 上传文件 ${file.name}`,
        }
      })
    }
    this.input.click();
  };

  executeUpload = (request, approvalId) => {
    this.setState({uploading: true, uploadStatus: 'active', percent: 0});
    const formData = new FormData();
    const token = uniqueId();
    this._updatePercent(token);
    formData.append('file', request.file);
    formData.append('id', request.hostId);
    formData.append('token', token);
    formData.append('path', request.path);
    formData.append('approval_id', approvalId);
    return http.post('/api/file/object/', formData, {timeout: 600000, onUploadProgress: this._updateLocal})
      .then(() => {
        this.setState({uploadStatus: 'success'});
        this.fetchFiles()
      })
      .catch(error => {
        if (this.socket) this.socket.close()
        this.setState({uploadStatus: 'exception'});
        return Promise.reject(error)
      })
      .finally(() => setTimeout(() => this.setState({uploading: false}), 2000))
  };

  _updateLocal = (e) => {
    const percent = e.loaded / e.total * 100 / 2
    this.setState({percent: Number(percent.toFixed(1))})
  }

  _updatePercent = token => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.socket = new WebSocket(`${protocol}//${window.location.host}/api/ws/subscribe/${token}/?x-token=${X_TOKEN}`);
    this.socket.onopen = () => this.socket.send('ok');
    this.socket.onmessage = e => {
      if (e.data === 'pong') {
        this.socket.send('ping')
      } else {
        const percent = this.state.percent + Number(e.data) / 2;
        if (percent > this.state.percent) this.setState({percent: Number(percent.toFixed(1))});
        if (percent === 100) {
          this.socket.close()
        }
      }
    }
  };

  handleDownload = (name) => {
    const file = `/${[...this.state.pwd, name].join('/')}`;
    const link = document.createElement('a');
    link.download = name;
    link.href = `/api/file/object/?id=${this.props.id}&file=${file}&x-token=${X_TOKEN}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    message.warning('即将开始下载，请勿重复点击。')
  };

  handleDelete = (name) => {
    const file = `/${[...this.state.pwd, name].join('/')}`;
    const hostId = Number(this.props.id);
    Modal.confirm({
      title: '删除文件确认',
      content: `确认删除文件：${file} ?`,
      onOk: () => {
        this.setState({
          approvalRequest: {
            operation: {
              action: 'file.delete',
              resource_type: 'host',
              resource_ids: [hostId],
              payload: {host_ids: [hostId], file},
            },
            hostId,
            file,
            summary: `删除主机 ${hostId} 文件 ${file}`,
          }
        })
      }
    })
  };

  executeDelete = (request, approvalId) => {
    return http.delete('/api/file/object/', {
      params: {id: request.hostId, file: request.file, approval_id: approvalId}
    }).then(() => {
      message.success('删除成功');
      this.fetchFiles()
    })
  };

  render() {
    const {approvalRequest} = this.state;
    let objects = this.state.objects;
    if (!this.state.showDot) {
      objects = objects.filter(x => !x.name.startsWith('.'))
    }
    const scrollY = document.body.clientHeight - 168;
    return (
      <React.Fragment>
        <input style={{display: 'none'}} type="file" ref={ref => this.input = ref}/>
        <div className={styles.drawerHeader}>
          {this.state.inputPath !== null ? (
            <Input size="small" className={styles.input}
                   suffix={<div style={{color: '#999', fontSize: 12}}>回车确认</div>}
                   value={this.state.inputPath} onChange={e => this.setState({inputPath: e.target.value})}
                   onBlur={this.handleInputEnter}
                   onPressEnter={this.handleInputEnter}/>
          ) : (
            <Breadcrumb className={styles.bread}>
              <Breadcrumb.Item href="#" onClick={() => this.handleChdir('', '0')}>
                <HomeOutlined style={{fontSize: 16}}/>
              </Breadcrumb.Item>
              {this.state.pwd.map(item => (
                <Breadcrumb.Item key={item} href="#" onClick={() => this.handleChdir(item, '2')}>
                  <span>{item}</span>
                </Breadcrumb.Item>
              ))}
              <Breadcrumb.Item onClick={this.handleInputEdit}>
                <EditOutlined className={styles.edit}/>
              </Breadcrumb.Item>
            </Breadcrumb>
          )}

          <div className={styles.action}>
            <span>显示隐藏文件：</span>
            <Switch
              checked={this.state.showDot}
              checkedChildren="开启"
              unCheckedChildren="关闭"
              onChange={v => this.setState({showDot: v})}/>
            {this.state.uploading ? (
              <Progress className={styles.progress} strokeWidth={14} status={this.state.uploadStatus}
                        percent={this.state.percent}/>
            ) : (
              <AuthButton
                auth="host.console.upload"
                style={{marginLeft: 12}}
                size="small"
                type="primary"
                icon={<UploadOutlined/>}
                onClick={this.handleUpload}>上传文件</AuthButton>
            )}
          </div>
        </div>
        <Table
          size="small"
          rowKey="name"
          loading={this.state.fetching}
          pagination={false}
          columns={this.columns}
          scroll={{y: scrollY}}
          style={{fontFamily: 'Source Code Pro, Courier New, Courier, Monaco, monospace, PingFang SC, Microsoft YaHei'}}
          dataSource={objects}/>
        {approvalRequest && (
          <ApprovalGate
            operation={approvalRequest.operation}
            defaultSummary={approvalRequest.summary}
            onCancel={() => this.setState({approvalRequest: null})}
            onExecute={approvalId => approvalRequest.operation.action === 'file.write'
              ? this.executeUpload(approvalRequest, approvalId)
              : this.executeDelete(approvalRequest, approvalId)}/>
        )}
      </React.Fragment>
    )
  }
}

export default FileManager
