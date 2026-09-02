/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Breadcrumb, Button, Table, Switch, Progress, Modal, Input, message } from 'antd';
import {
  CloseCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  HomeOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  RedoOutlined,
  UploadOutlined,
  EditOutlined
} from '@ant-design/icons';
import { ACEditor, ApprovalGate, AuthButton, Action } from 'components';
import { http, X_TOKEN } from 'libs';
import { sha256 } from 'js-sha256';
import lds from 'lodash';
import styles from './index.module.less'
import moment from 'moment';


const UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024;
const BATCH_MAX_FILES = 50;
const BATCH_MAX_CONCURRENCY = 2;


const readBlob = blob => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
  reader.readAsArrayBuffer(blob);
});


const compareFilenames = (left, right) => {
  const a = Array.from(left);
  const b = Array.from(right);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const difference = a[index].codePointAt(0) - b[index].codePointAt(0);
    if (difference) return difference;
  }
  return a.length - b.length;
};


class FileManager extends React.Component {
  constructor(props) {
    super(props);
    this.input = null;
    this.batchInput = null;
    this.batchContext = null;
    this.editHeartbeat = null;
    this.pwdHistoryCaches = new Map()
    this.state = {
      fetching: false,
      showDot: false,
      uploading: false,
      preparingUpload: false,
      preparingBatch: false,
      inputPath: null,
      uploadStatus: 'active',
      approvalRequest: null,
      pwd: [],
      objects: [],
      percent: 0,
      selectedRowKeys: [],
      batchId: null,
      batchStatus: null,
      batchItems: [],
      batchPercent: 0,
      batchModalVisible: false,
      batchRunning: false,
      batchError: null,
      editSession: null,
      editContent: '',
      editOriginalContent: '',
      editModalVisible: false,
      editLoading: false,
    }
  }

  componentDidMount() {
    this.fetchFiles()
  }

  componentDidUpdate(prevProps) {
    if (this.props.id !== prevProps.id) {
      this._stopEditHeartbeat();
      const session = this.state.editSession;
      if (session && session.status === 'active') {
        http.delete(`/api/file/edit-sessions/${session.session_id}/`).catch(() => null);
      }
      let pwd = this.pwdHistoryCaches.get(this.props.id) || []
      this.setState({
        objects: [],
        pwd,
        editSession: null,
        editContent: '',
        editOriginalContent: '',
        editModalVisible: false,
        approvalRequest: this.state.approvalRequest
          && this.state.approvalRequest.kind === 'file-edit'
          ? null
          : this.state.approvalRequest,
      })
      this.fetchFiles(pwd)
    }
  }

  componentWillUnmount() {
    if (this.batchContext) this.batchContext.stopRequested = true;
    this._stopEditHeartbeat();
    const session = this.state.editSession;
    if (session && session.status === 'active') {
      http.delete(`/api/file/edit-sessions/${session.session_id}/`).catch(() => null);
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
    width: 140,
    align: 'right',
    key: 'action',
    render: info => info.kind === '-' ? (
      <Action>
        <Action.Button auth="host.console.upload" className={styles.drawerBtn}
                       icon={<EditOutlined/>} onClick={() => this.handleEdit(info.name)}/>
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
        this.setState({objects, pwd, selectedRowKeys: []})
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

  handleBatchUpload = () => {
    this.batchInput.onchange = async e => {
      const selected = Array.from(e.target.files || []);
      this.batchInput.value = '';
      if (!selected.length) return;
      if (selected.length > BATCH_MAX_FILES) {
        message.error(`每批最多选择 ${BATCH_MAX_FILES} 个文件`);
        return;
      }
      const names = new Set(selected.map(file => file.name));
      if (names.size !== selected.length) {
        message.error('同一批次不能包含同名文件');
        return;
      }

      const hostId = Number(this.props.id);
      const path = '/' + this.state.pwd.join('/');
      const hideProgress = message.loading('正在逐个计算文件 SHA-256，请稍候...', 0);
      this.setState({preparingBatch: true});
      try {
        const files = [];
        for (const file of selected) {
          files.push({
            file,
            filename: file.name,
            size: file.size,
            sha256: await this._hashFile(file),
          });
        }
        files.sort((a, b) => compareFilenames(a.filename, b.filename));
        const payload = {
          host_ids: [hostId],
          path,
          files: files.map(item => ({
            filename: item.filename,
            size: item.size,
            sha256: item.sha256,
          })),
          chunk_size: UPLOAD_CHUNK_SIZE,
          conflict_strategy: 'overwrite',
          max_concurrency: BATCH_MAX_CONCURRENCY,
        };
        const request = {
          kind: 'batch-upload',
          operation: {
            action: 'file.write',
            resource_type: 'host',
            resource_ids: [hostId],
            payload,
          },
          files,
          hostId,
          path,
          summary: `向主机 ${hostId} 的 ${path} 批量上传 ${files.length} 个文件`,
        };

        const storedBatchId = this._getStoredBatchId(request);
        if (storedBatchId) {
          try {
            const batch = await http.get(`/api/file/upload-batches/${storedBatchId}/`);
            if (this._canResumeBatch(request, batch)) {
              this._activateBatch(request, batch);
              message.info('已恢复相同文件集的批量上传任务。');
              return;
            }
          } catch (error) {
            // The persisted id is only a resume hint; stale entries are discarded.
          }
          this._clearStoredBatch(request);
        }
        this.setState({approvalRequest: request});
      } catch (error) {
        message.error('无法读取所选文件并计算 SHA-256');
      } finally {
        hideProgress();
        this.setState({preparingBatch: false});
      }
    };
    this.batchInput.click();
  };

  executeBatchUpload = async (request, approvalId) => {
    const payload = request.operation.payload;
    const batch = await http.post('/api/file/upload-batches/', {
      id: request.hostId,
      path: request.path,
      files: payload.files,
      chunk_size: payload.chunk_size,
      conflict_strategy: payload.conflict_strategy,
      max_concurrency: payload.max_concurrency,
      approval_id: approvalId,
    });
    this._storeBatchId(request, batch.id);
    this._activateBatch(request, batch);
    return batch;
  };

  _activateBatch = (request, batch) => {
    const filesByName = new Map(request.files.map(item => [item.filename, item.file]));
    const context = {
      request,
      batch,
      items: batch.transfers.map(session => ({
        file: filesByName.get(session.filename),
        session,
      })),
      stopRequested: batch.status !== 'active',
      cancelled: batch.status === 'cancelled',
      running: false,
      error: batch.error,
    };
    if (context.items.some(item => !item.file)) {
      this._clearStoredBatch(request);
      message.error('本地文件集与服务端批次不一致，无法恢复');
      return;
    }
    this.batchContext = context;
    this._syncBatchState(context, {visible: true});
    if (batch.status === 'active') {
      setTimeout(() => this._runBatch(context), 0);
    }
  };

  _syncBatchState = (context, options = {}) => {
    const batch = context.batch;
    const sessions = context.items.map(item => item.session);
    const totalSize = sessions.reduce((total, item) => total + item.size, 0);
    let percent;
    if (batch.status === 'completed') {
      percent = 100;
    } else if (totalSize) {
      const uploaded = sessions.reduce(
        (total, item) => total + Math.min(item.offset, item.size), 0
      );
      percent = Math.min(uploaded / totalSize * 100, 99.9);
    } else {
      const completed = sessions.filter(item => item.status === 'completed').length;
      percent = sessions.length ? completed / sessions.length * 100 : 0;
    }
    this.setState(previous => ({
      batchId: batch.id,
      batchStatus: batch.status,
      batchItems: sessions.map(item => ({...item})),
      batchPercent: Number(percent.toFixed(1)),
      batchRunning: context.running,
      batchError: context.error || batch.error,
      batchModalVisible: options.visible === undefined
        ? previous.batchModalVisible
        : options.visible,
    }));
  };

  _mergeBatchState = (context, batch) => {
    const sessionsById = new Map(batch.transfers.map(item => [item.id, item]));
    context.items.forEach(item => {
      item.session = sessionsById.get(item.session.id) || item.session;
    });
    context.batch = batch;
    context.cancelled = batch.status === 'cancelled';
    if (batch.error) context.error = batch.error;
    this._syncBatchState(context);
  };

  _runBatch = async context => {
    if (context !== this.batchContext || context.running || context.batch.status !== 'active') {
      return;
    }
    context.running = true;
    context.stopRequested = false;
    context.error = null;
    this._syncBatchState(context);
    const pending = context.items.filter(item => item.session.status === 'active');
    let cursor = 0;
    let networkError = null;
    const worker = async () => {
      while (!context.stopRequested && cursor < pending.length) {
        const item = pending[cursor];
        cursor += 1;
        try {
          await this._uploadBatchFile(context, item);
        } catch (error) {
          networkError = networkError || error;
          context.stopRequested = true;
        }
      }
    };
    const workerCount = Math.min(context.batch.max_concurrency, pending.length);
    await Promise.all(Array.from({length: workerCount}, () => worker()));
    context.running = false;
    if (context !== this.batchContext || context.cancelled) return;

    if (networkError) {
      await this._pauseBatchAfterFailure(context);
      return;
    }
    try {
      const latest = await http.get(`/api/file/upload-batches/${context.batch.id}/`);
      this._mergeBatchState(context, latest);
      if (latest.status === 'completed') {
        this._clearStoredBatch(context.request);
        message.success(`批量上传完成，共 ${latest.file_count} 个文件`);
        this.fetchFiles();
      } else if (latest.status === 'failed') {
        message.warning('批量上传中存在失败文件，可在进度面板中重试。');
      }
    } catch (error) {
      await this._pauseBatchAfterFailure(context);
    }
  };

  _uploadBatchFile = async (context, item) => {
    let session = item.session;
    try {
      while (session.offset < item.file.size) {
        if (context.stopRequested) return;
        const chunkOffset = session.offset;
        const chunk = item.file.slice(
          chunkOffset,
          Math.min(chunkOffset + session.chunk_size, item.file.size),
        );
        const formData = new FormData();
        formData.append('offset', chunkOffset);
        formData.append('sha256', sha256(await readBlob(chunk)));
        formData.append('chunk', chunk, item.file.name);
        try {
          session = await http.post(
            `/api/file/uploads/${session.id}/chunk/`,
            formData,
            {timeout: 600000},
          );
        } catch (error) {
          const latest = await this._recoverUploadStatus(session.id);
          if (latest && latest.status === 'active' && latest.offset > chunkOffset) {
            session = latest;
          } else if (latest && latest.status !== 'active') {
            session = latest;
            item.session = session;
            this._syncBatchState(context);
            return;
          } else {
            throw error;
          }
        }
        item.session = session;
        this._syncBatchState(context);
      }
      if (context.stopRequested) return;
      session = await http.post(`/api/file/uploads/${session.id}/complete/`);
      item.session = session;
      this._syncBatchState(context);
    } catch (error) {
      const latest = await this._recoverUploadStatus(session.id);
      if (latest && latest.status !== 'active') {
        item.session = latest;
        this._syncBatchState(context);
        return;
      }
      throw error;
    }
  };

  _pauseBatchAfterFailure = async context => {
    let latest = null;
    try {
      latest = await http.get(`/api/file/upload-batches/${context.batch.id}/`);
      if (latest.status === 'active') {
        latest = await http.patch(`/api/file/upload-batches/${context.batch.id}/`, {
          action: 'pause',
        });
      }
    } catch (error) {
      // Keep the local stop flag and persisted id when the control request also fails.
    }
    if (latest) this._mergeBatchState(context, latest);
    context.error = latest && latest.status === 'paused'
      ? '网络异常，任务已安全暂停'
      : '网络异常，本地上传已停止；请重新连接后核对服务端状态';
    this._syncBatchState(context);
    message.warning(context.error);
  };

  controlBatch = async action => {
    const context = this.batchContext;
    if (!context) return;
    if (action === 'pause') context.stopRequested = true;
    try {
      const batch = await http.patch(`/api/file/upload-batches/${context.batch.id}/`, {
        action,
      });
      this._mergeBatchState(context, batch);
      context.error = null;
      this._syncBatchState(context);
      if (action === 'pause') {
        message.info('当前分片完成后已暂停批量上传');
      } else {
        context.stopRequested = false;
        this._syncBatchState(context);
        this._runBatch(context);
      }
    } catch (error) {
      context.error = action === 'pause' ? '暂停请求失败' : '批量上传操作失败';
      this._syncBatchState(context);
    }
  };

  reconnectBatch = async () => {
    const context = this.batchContext;
    if (!context) return;
    try {
      const latest = await http.get(`/api/file/upload-batches/${context.batch.id}/`);
      this._mergeBatchState(context, latest);
      context.error = null;
      if (latest.status === 'active') {
        context.stopRequested = false;
        this._runBatch(context);
      } else if (latest.status === 'paused') {
        await this.controlBatch('resume');
      }
    } catch (error) {
      context.error = '仍无法连接服务端，请稍后重试';
      this._syncBatchState(context);
    }
  };

  cancelBatch = () => {
    const context = this.batchContext;
    if (!context) return;
    Modal.confirm({
      title: '取消批量上传',
      content: '未完成的文件将停止上传，远端临时文件由清理任务安全删除。确认继续？',
      onOk: async () => {
        context.stopRequested = true;
        context.cancelled = true;
        try {
          const batch = await http.delete(`/api/file/upload-batches/${context.batch.id}/`);
          this._mergeBatchState(context, batch);
          this._clearStoredBatch(context.request);
          message.info('批量上传已取消，临时文件已进入清理队列');
        } catch (error) {
          context.cancelled = false;
          context.error = '取消请求失败，请重新连接后核对任务状态';
          this._syncBatchState(context);
          return Promise.reject(error);
        }
      },
    });
  };

  _batchStorageKey = request => {
    const fingerprint = sha256(JSON.stringify(request.files.map(item => ({
      filename: item.filename,
      size: item.size,
      sha256: item.sha256,
    }))));
    return `spug:file-upload-batch:${request.hostId}:${encodeURIComponent(request.path)}:${fingerprint}`;
  };

  _getStoredBatchId = request => {
    try {
      return window.localStorage.getItem(this._batchStorageKey(request));
    } catch (error) {
      return null;
    }
  };

  _storeBatchId = (request, batchId) => {
    try {
      window.localStorage.setItem(this._batchStorageKey(request), batchId);
    } catch (error) {
      // Upload remains available without cross-refresh resume support.
    }
  };

  _clearStoredBatch = request => {
    try {
      window.localStorage.removeItem(this._batchStorageKey(request));
    } catch (error) {
      // Ignore unavailable browser storage.
    }
  };

  _canResumeBatch = (request, batch) => {
    const allowedStatuses = ['active', 'paused', 'failed'];
    if (!allowedStatuses.includes(batch.status)
      || batch.host_id !== request.hostId
      || batch.directory !== request.path
      || batch.max_concurrency !== BATCH_MAX_CONCURRENCY
      || batch.transfers.length !== request.files.length) {
      return false;
    }
    const expected = request.files;
    const transfers = [...batch.transfers].sort(
      (a, b) => compareFilenames(a.filename, b.filename)
    );
    return transfers.every((session, index) => (
      session.filename === expected[index].filename
      && session.size === expected[index].size
      && session.expected_sha256 === expected[index].sha256
      && session.chunk_size === UPLOAD_CHUNK_SIZE
      && session.conflict_strategy === 'overwrite'
    ));
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

  handleEdit = async name => {
    const remotePath = `/${[...this.state.pwd, name].join('/')}`;
    this.setState({editLoading: true});
    try {
      const session = await http.post('/api/file/edit-sessions/', {
        id: Number(this.props.id),
        file: remotePath,
      });
      this.setState({
        editSession: session,
        editContent: session.content,
        editOriginalContent: session.content,
        editModalVisible: true,
      });
      this._startEditHeartbeat(session.session_id);
    } catch (error) {
      return null;
    } finally {
      this.setState({editLoading: false});
    }
  };

  _startEditHeartbeat = sessionId => {
    this._stopEditHeartbeat();
    this.editHeartbeat = window.setInterval(() => {
      http.patch(`/api/file/edit-sessions/${sessionId}/`, {
        action: 'heartbeat',
      }).then(session => {
        if (this.state.editSession
          && this.state.editSession.session_id === sessionId) {
          this.setState({editSession: {...this.state.editSession, ...session}});
        }
      }).catch(() => {
        this._stopEditHeartbeat();
        message.warning('在线编辑锁续期失败，保存前请重新打开文件核对版本');
      });
    }, 5 * 60 * 1000);
  };

  _stopEditHeartbeat = () => {
    if (this.editHeartbeat) {
      window.clearInterval(this.editHeartbeat);
      this.editHeartbeat = null;
    }
  };

  releaseEdit = async () => {
    const session = this.state.editSession;
    this._stopEditHeartbeat();
    if (session && session.status === 'active') {
      await http.delete(`/api/file/edit-sessions/${session.session_id}/`);
    }
    this.setState({
      editSession: null,
      editContent: '',
      editOriginalContent: '',
      editModalVisible: false,
      approvalRequest: this.state.approvalRequest
        && this.state.approvalRequest.kind === 'file-edit'
        ? null
        : this.state.approvalRequest,
    });
  };

  requestCloseEdit = () => {
    if (this.state.editContent !== this.state.editOriginalContent) {
      Modal.confirm({
        title: '放弃未保存的修改？',
        content: '关闭后将释放编辑锁，本地修改不会写入远端文件。',
        onOk: this.releaseEdit,
      });
    } else {
      this.releaseEdit().catch(() => null);
    }
  };

  prepareEditSave = () => {
    const session = this.state.editSession;
    if (!session || session.status !== 'active') {
      message.error('在线编辑锁已失效，请重新打开文件');
      return;
    }
    const content = this.state.editContent;
    const size = new Blob([content]).size;
    if (size > 2 * 1024 * 1024) {
      message.error('在线编辑内容不能超过 2 MiB');
      return;
    }
    const contentSha256 = sha256(content);
    const payload = {
      host_ids: [session.host_id],
      file: session.remote_path,
      base_etag: session.base_etag,
      base_sha256: session.base_sha256,
      size,
      sha256: contentSha256,
      encoding: 'utf-8',
      conflict_strategy: 'reject_on_change',
    };
    this.setState({
      approvalRequest: {
        kind: 'file-edit',
        operation: {
          action: 'file.write',
          resource_type: 'host',
          resource_ids: [session.host_id],
          payload,
        },
        editSession: session,
        editContent: content,
        editSha256: contentSha256,
        summary: `在线编辑主机 ${session.host_id} 文件 ${session.remote_path}`,
      },
    });
  };

  executeEditSave = async (request, approvalId) => {
    try {
      const session = await http.post(
        `/api/file/edit-sessions/${request.editSession.session_id}/save/`,
        {
          content: request.editContent,
          sha256: request.editSha256,
          base_etag: request.editSession.base_etag,
          base_sha256: request.editSession.base_sha256,
          approval_id: approvalId,
        },
      );
      this._stopEditHeartbeat();
      this.setState({
        editSession: session,
        editOriginalContent: request.editContent,
        editModalVisible: false,
        approvalRequest: null,
      });
      message.success(`文件已安全保存，SHA-256：${request.editSha256}`);
      await this.fetchFiles();
      return session;
    } catch (error) {
      this.setState({approvalRequest: null});
      return Promise.reject(error);
    }
  };

  _editMode = filename => {
    const extension = (filename.split('.').pop() || '').toLowerCase();
    if (extension === 'json') return 'json';
    if (extension === 'py') return 'python';
    if (['sh', 'bash', 'zsh'].includes(extension)) return 'sh';
    return 'text';
  };

  _downloadFile = name => {
    const file = `/${[...this.state.pwd, name].join('/')}`;
    const link = document.createElement('a');
    link.download = name;
    link.href = `/api/file/object/?id=${encodeURIComponent(this.props.id)}&file=${encodeURIComponent(file)}&x-token=${encodeURIComponent(X_TOKEN)}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  handleDownload = (name) => {
    this._downloadFile(name);
    message.info('下载已开始；服务端支持 HTTP Range，可使用浏览器或下载工具断点续传。')
  };

  handleBatchDownload = () => {
    const names = this.state.selectedRowKeys.filter(name => (
      this.state.objects.some(item => item.kind === '-' && item.name === name)
    ));
    if (!names.length) {
      message.info('请先选择需要下载的文件');
      return;
    }
    Modal.confirm({
      title: `批量下载 ${names.length} 个文件`,
      content: '浏览器可能会询问是否允许此站点下载多个文件；请允许后继续。每个文件都支持 HTTP Range 断点续传。',
      onOk: () => {
        names.forEach(this._downloadFile);
        message.info(`已发起 ${names.length} 个下载任务`);
      },
    });
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
    const batchLocked = ['active', 'paused', 'failed'].includes(this.state.batchStatus);
    const batchStatusNames = {
      active: '上传中',
      paused: '已暂停',
      completed: '已完成',
      failed: '失败',
      cancelled: '已取消',
      expired: '已过期',
    };
    const batchColumns = [{
      title: '文件',
      dataIndex: 'filename',
      ellipsis: true,
    }, {
      title: '进度',
      key: 'progress',
      width: 230,
      render: item => {
        const percent = item.status === 'completed'
          ? 100
          : item.size ? Math.min(item.offset / item.size * 100, 99.9) : 0;
        return (
          <Progress
            percent={Number(percent.toFixed(1))}
            size="small"
            status={item.status === 'failed' || item.status === 'expired'
              ? 'exception'
              : item.status === 'completed' ? 'success' : 'normal'}/>
        )
      },
    }, {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: value => batchStatusNames[value] || value,
    }, {
      title: '说明',
      dataIndex: 'error',
      ellipsis: true,
    }];
    return (
      <React.Fragment>
        <input style={{display: 'none'}} type="file" ref={ref => this.input = ref}/>
        <input style={{display: 'none'}} type="file" multiple
               ref={ref => this.batchInput = ref}/>
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
            {this.state.selectedRowKeys.length > 0 && (
              <Button size="small" style={{marginLeft: 12}} icon={<DownloadOutlined/>}
                      onClick={this.handleBatchDownload}>
                批量下载({this.state.selectedRowKeys.length})
              </Button>
            )}
            {this.state.batchId && (
              <Button size="small" style={{marginLeft: 12}}
                      onClick={() => this.setState({batchModalVisible: true})}>
                批量进度
              </Button>
            )}
            {this.state.uploading ? (
              <Progress className={styles.progress} strokeWidth={14} status={this.state.uploadStatus}
                        percent={this.state.percent}/>
            ) : (
              <React.Fragment>
                <AuthButton
                  auth="host.console.upload"
                  style={{marginLeft: 12}}
                  size="small"
                  disabled={batchLocked}
                  loading={this.state.preparingBatch}
                  icon={<UploadOutlined/>}
                  onClick={this.handleBatchUpload}>批量上传</AuthButton>
                <AuthButton
                  auth="host.console.upload"
                  style={{marginLeft: 8}}
                  size="small"
                  type="primary"
                  disabled={this.state.batchRunning}
                  loading={this.state.preparingUpload}
                  icon={<UploadOutlined/>}
                  onClick={this.handleUpload}>上传文件</AuthButton>
              </React.Fragment>
            )}
          </div>
        </div>
        <Table
          size="small"
          rowKey="name"
          loading={this.state.fetching}
          pagination={false}
          rowSelection={{
            selectedRowKeys: this.state.selectedRowKeys,
            onChange: selectedRowKeys => this.setState({selectedRowKeys}),
            getCheckboxProps: item => ({disabled: item.kind !== '-'}),
          }}
          columns={this.columns}
          scroll={{y: scrollY}}
          style={{fontFamily: 'Source Code Pro, Courier New, Courier, Monaco, monospace, PingFang SC, Microsoft YaHei'}}
          dataSource={objects}/>
        <Modal
          title={`批量上传进度${this.state.batchId ? ` · ${this.state.batchId}` : ''}`}
          visible={this.state.batchModalVisible}
          width={820}
          maskClosable={false}
          onCancel={() => this.setState({batchModalVisible: false})}
          footer={(
            <div>
              {this.state.batchStatus === 'active' && this.state.batchRunning && (
                <Button icon={<PauseCircleOutlined/>}
                        onClick={() => this.controlBatch('pause')}>暂停</Button>
              )}
              {this.state.batchStatus === 'active' && !this.state.batchRunning && (
                <Button icon={<RedoOutlined/>}
                        onClick={this.reconnectBatch}>重新连接</Button>
              )}
              {this.state.batchStatus === 'paused' && (
                <Button type="primary" icon={<PlayCircleOutlined/>}
                        onClick={() => this.controlBatch('resume')}>继续</Button>
              )}
              {this.state.batchStatus === 'failed' && (
                <Button type="primary" icon={<RedoOutlined/>}
                        onClick={() => this.controlBatch('retry')}>重试失败文件</Button>
              )}
              {!['completed', 'cancelled'].includes(this.state.batchStatus) && (
                <Button danger icon={<CloseCircleOutlined/>}
                        onClick={this.cancelBatch}>取消任务</Button>
              )}
              <Button onClick={() => this.setState({batchModalVisible: false})}>关闭</Button>
            </div>
          )}>
          <div style={{marginBottom: 12}}>
            <span style={{marginRight: 16}}>
              状态：{batchStatusNames[this.state.batchStatus] || '-'}
            </span>
            {this.state.batchError && (
              <span style={{color: '#f5222d'}}>{this.state.batchError}</span>
            )}
          </div>
          <Progress
            percent={this.state.batchPercent}
            status={this.state.batchStatus === 'failed'
              ? 'exception'
              : this.state.batchStatus === 'completed' ? 'success' : 'normal'}/>
          <Table
            style={{marginTop: 12}}
            size="small"
            rowKey="id"
            pagination={false}
            scroll={{y: 360}}
            columns={batchColumns}
            dataSource={this.state.batchItems}/>
        </Modal>
        <Modal
          title={this.state.editSession
            ? `在线编辑 · ${this.state.editSession.remote_path}`
            : '在线编辑'}
          visible={this.state.editModalVisible}
          width={920}
          maskClosable={false}
          onCancel={this.requestCloseEdit}
          footer={(
            <div>
              <Button onClick={this.requestCloseEdit}>取消并释放锁</Button>
              <Button type="primary" loading={this.state.editLoading}
                      disabled={!this.state.editSession
                        || this.state.editContent === this.state.editOriginalContent}
                      onClick={this.prepareEditSave}>
                审批并保存
              </Button>
            </div>
          )}>
          {this.state.editSession && (
            <React.Fragment>
              <div style={{marginBottom: 10, color: '#666'}}>
                <span style={{marginRight: 16}}>
                  基础 SHA-256：{this.state.editSession.base_sha256}
                </span>
                <span>锁有效至：{this.state.editSession.expires_at}</span>
              </div>
              <ACEditor
                mode={this._editMode(this.state.editSession.filename)}
                value={this.state.editContent}
                width="100%"
                height="520px"
                setOptions={{useWorker: false}}
                onChange={editContent => this.setState({editContent})}/>
              <div style={{marginTop: 8, color: '#999'}}>
                仅支持 UTF-8 普通文本且最大 2 MiB；保存前会再次校验远端版本，外部修改不会被覆盖。
              </div>
            </React.Fragment>
          )}
        </Modal>
        {approvalRequest && (
          <ApprovalGate
            operation={approvalRequest.operation}
            defaultSummary={approvalRequest.summary}
            onCancel={() => this.setState({approvalRequest: null})}
            onExecute={approvalId => approvalRequest.operation.action === 'file.write'
              ? approvalRequest.kind === 'batch-upload'
                ? this.executeBatchUpload(approvalRequest, approvalId)
                : approvalRequest.kind === 'file-edit'
                  ? this.executeEditSave(approvalRequest, approvalId)
                  : this.executeUpload(approvalRequest, approvalId)
              : this.executeDelete(approvalRequest, approvalId)}/>
        )}
      </React.Fragment>
    )
  }
}

export default FileManager
