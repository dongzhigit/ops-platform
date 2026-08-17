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
import { http, X_TOKEN } from 'libs';
import { sha256 } from 'js-sha256';
import lds from 'lodash';
import styles from './index.module.less'
import moment from 'moment';


const UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024;


const readBlob = blob => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
  reader.readAsArrayBuffer(blob);
});


class FileManager extends React.Component {
  constructor(props) {
    super(props);
    this.input = null;
    this.pwdHistoryCaches = new Map()
    this.state = {
      fetching: false,
      showDot: false,
      uploading: false,
      preparingUpload: false,
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
    this.input.onchange = async e => {
      const file = e.target['files'][0];
      this.input.value = '';
      if (!file) return;
      const hostId = Number(this.props.id);
      const path = '/' + this.state.pwd.join('/');
      const hideProgress = message.loading('正在计算文件 SHA-256，请稍候...', 0);
      this.setState({preparingUpload: true});
      try {
        const fileSha256 = await this._hashFile(file);
        const payload = {
          host_ids: [hostId],
          path,
          filename: file.name,
          size: file.size,
          sha256: fileSha256,
          chunk_size: UPLOAD_CHUNK_SIZE,
          conflict_strategy: 'overwrite',
        };
        const request = {
          operation: {
            action: 'file.write',
            resource_type: 'host',
            resource_ids: [hostId],
            payload,
          },
          file,
          fileSha256,
          hostId,
          path,
          summary: `向主机 ${hostId} 的 ${path} 上传文件 ${file.name}`,
        };
        const storedUploadId = this._getStoredUploadId(request);
        if (storedUploadId) {
          try {
            const session = await http.get(`/api/file/uploads/${storedUploadId}/`);
            if (this._canResume(request, session)) {
              message.info(`已找到上传进度，将从 ${session.offset} 字节处继续。`);
              this.executeUpload(request, null, session).catch(() => null);
              return;
            }
          } catch (e) {
            // Invalid and expired sessions are discarded below so a new approval can be used.
          }
          this._clearStoredUpload(request);
        }
        this.setState({
          approvalRequest: request
        });
      } catch (e) {
        message.error('无法读取文件并计算 SHA-256');
      } finally {
        hideProgress();
        this.setState({preparingUpload: false});
      }
    };
    this.input.click();
  };

  executeUpload = async (request, approvalId, existingSession = null) => {
    this.setState({uploading: true, uploadStatus: 'active', percent: 0});
    let session = existingSession;
    try {
      if (!session) {
        const payload = request.operation.payload;
        session = await http.post('/api/file/uploads/', {
          id: request.hostId,
          path: request.path,
          filename: request.file.name,
          size: request.file.size,
          sha256: request.fileSha256,
          chunk_size: payload.chunk_size,
          conflict_strategy: payload.conflict_strategy,
          approval_id: approvalId,
        });
        this._storeUploadId(request, session.id);
      }

      let offset = session.offset;
      this._setUploadPercent(offset, request.file.size);
      while (offset < request.file.size) {
        const chunkOffset = offset;
        const chunk = request.file.slice(
          chunkOffset, Math.min(chunkOffset + session.chunk_size, request.file.size)
        );
        const chunkSha256 = sha256(await readBlob(chunk));
        const formData = new FormData();
        formData.append('offset', chunkOffset);
        formData.append('sha256', chunkSha256);
        formData.append('chunk', chunk, request.file.name);
        try {
          session = await http.post(
            `/api/file/uploads/${session.id}/chunk/`,
            formData,
            {
              timeout: 600000,
              onUploadProgress: event => {
                this._setUploadPercent(chunkOffset + event.loaded, request.file.size)
              }
            }
          );
        } catch (error) {
          const latest = await this._recoverUploadStatus(session.id);
          if (!latest || latest.status !== 'active' || latest.offset <= chunkOffset) {
            throw error;
          }
          session = latest;
        }
        offset = session.offset;
        this._setUploadPercent(offset, request.file.size);
      }

      session = await http.post(`/api/file/uploads/${session.id}/complete/`);
      this._clearStoredUpload(request);
      this.setState({uploadStatus: 'success', percent: 100});
      message.success(`上传完成，SHA-256：${session.actual_sha256}`);
      await this.fetchFiles();
      return session;
    } catch (error) {
      this.setState({uploadStatus: 'exception'});
      message.warning('上传已暂停，重新选择同一文件可从断点继续。');
      if (session && session.id) {
        // The approval has already created a resumable server-side session.
        // Resolve so ApprovalGate closes instead of asking for a second approval.
        return {...session, paused: true}
      }
      return Promise.reject(error)
    } finally {
      setTimeout(() => this.setState({uploading: false}), 2000)
    }
  };

  _hashFile = async file => {
    const digest = sha256.create();
    for (let offset = 0; offset < file.size; offset += UPLOAD_CHUNK_SIZE) {
      digest.update(await readBlob(file.slice(offset, offset + UPLOAD_CHUNK_SIZE)));
    }
    return digest.hex();
  };

  _uploadStorageKey = request => (
    `spug:file-upload:${request.hostId}:${encodeURIComponent(request.path)}:${request.fileSha256}`
  );

  _getStoredUploadId = request => {
    try {
      return window.localStorage.getItem(this._uploadStorageKey(request));
    } catch (e) {
      return null;
    }
  };

  _storeUploadId = (request, uploadId) => {
    try {
      window.localStorage.setItem(this._uploadStorageKey(request), uploadId);
    } catch (e) {
      // Upload still works when storage is unavailable; only cross-refresh resume is disabled.
    }
  };

  _clearStoredUpload = request => {
    try {
      window.localStorage.removeItem(this._uploadStorageKey(request));
    } catch (e) {
      // Ignore unavailable browser storage.
    }
  };

  _canResume = (request, session) => {
    return session.status === 'active'
      && session.host_id === request.hostId
      && session.directory === request.path
      && session.filename === request.file.name
      && session.size === request.file.size
      && session.expected_sha256 === request.fileSha256;
  };

  _recoverUploadStatus = uploadId => {
    return http.get(`/api/file/uploads/${uploadId}/`).catch(() => null)
  };

  _setUploadPercent = (offset, total) => {
    const percent = total ? Math.min(offset / total * 100, 99.9) : 99.9;
    this.setState({percent: Number(percent.toFixed(1))})
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
                loading={this.state.preparingUpload}
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
