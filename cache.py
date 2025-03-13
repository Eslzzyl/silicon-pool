import threading
import time
import logging
from typing import Dict, Any, List, Tuple
import sqlite3

logger = logging.getLogger(__name__)


class DatabaseCache:
    """数据库写操作的内存缓存系统"""

    def __init__(
        self,
        db_connection: sqlite3.Connection,
        flush_interval: int = 60,
        max_batch_size: int = 100,
    ):
        """
        初始化缓存系统

        参数:
            db_connection: SQLite数据库连接
            flush_interval: 自动刷新到数据库的间隔(秒)
            max_batch_size: 触发自动刷新的最大缓存条目数
        """
        self.conn = db_connection
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size

        # 缓存存储
        self._insert_cache: Dict[str, List[Tuple]] = {}  # 表名 -> 待插入数据列表
        self._update_cache: Dict[str, List[Dict]] = {}  # 表名 -> 待更新数据列表
        self._delete_cache: Dict[str, List[Any]] = {}  # 表名 -> 待删除记录的主键列表

        # 线程安全锁
        self._cache_lock = threading.RLock()

        # 统计信息
        self.stats = {
            "inserts_cached": 0,
            "updates_cached": 0,
            "deletes_cached": 0,
            "flushes": 0,
            "last_flush": 0,
        }

        # 启动后台自动刷新线程
        self._stop_event = threading.Event()
        self._flush_thread = threading.Thread(
            target=self._auto_flush_worker, daemon=True
        )
        self._flush_thread.start()

    def queue_insert(self, table: str, data: Tuple) -> None:
        """
        将插入操作添加到缓存

        参数:
            table: 表名
            data: 要插入的数据元组
        """
        with self._cache_lock:
            if table not in self._insert_cache:
                self._insert_cache[table] = []

            self._insert_cache[table].append(data)
            self.stats["inserts_cached"] += 1

            # 检查是否需要刷新
            if self._should_auto_flush():
                self.flush()

    def queue_update(
        self,
        table: str,
        update_data: Dict[str, Any],
        where_field: str,
        where_value: Any,
    ) -> None:
        """
        将更新操作添加到缓存

        参数:
            table: 表名
            update_data: 要更新的字段和值的字典
            where_field: WHERE条件的字段名
            where_value: WHERE条件的值
        """
        with self._cache_lock:
            if table not in self._update_cache:
                self._update_cache[table] = []

            self._update_cache[table].append(
                {
                    "data": update_data,
                    "where_field": where_field,
                    "where_value": where_value,
                }
            )
            self.stats["updates_cached"] += 1

            # 检查是否需要刷新
            if self._should_auto_flush():
                self.flush()

    def queue_delete(
        self, table: str, primary_key_field: str, primary_key_value: Any
    ) -> None:
        """
        将删除操作添加到缓存

        参数:
            table: 表名
            primary_key_field: 主键字段名
            primary_key_value: 主键值
        """
        with self._cache_lock:
            if table not in self._delete_cache:
                self._delete_cache[table] = []

            self._delete_cache[table].append(
                {"field": primary_key_field, "value": primary_key_value}
            )
            self.stats["deletes_cached"] += 1

            # 检查是否需要刷新
            if self._should_auto_flush():
                self.flush()

    def flush(self) -> None:
        """将缓存的所有操作刷新到数据库"""
        with self._cache_lock:
            try:
                cursor = self.conn.cursor()

                # 处理插入操作
                for table, rows in self._insert_cache.items():
                    if not rows:
                        continue

                    # 获取列数量以构建参数占位符
                    placeholders = ",".join(["?"] * len(rows[0]))
                    query = f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})"

                    # 批量执行
                    cursor.executemany(query, rows)

                # 处理更新操作
                for table, updates in self._update_cache.items():
                    if not updates:
                        continue

                    for update in updates:
                        set_clause = ", ".join(
                            [f"{field} = ?" for field in update["data"].keys()]
                        )
                        params = list(update["data"].values()) + [update["where_value"]]

                        query = f"UPDATE {table} SET {set_clause} WHERE {update['where_field']} = ?"
                        cursor.execute(query, params)

                # 处理删除操作
                for table, deletes in self._delete_cache.items():
                    if not deletes:
                        continue

                    for delete_op in deletes:
                        query = f"DELETE FROM {table} WHERE {delete_op['field']} = ?"
                        cursor.execute(query, (delete_op["value"],))

                # 提交事务
                self.conn.commit()

                # 清空缓存
                self._insert_cache.clear()
                self._update_cache.clear()
                self._delete_cache.clear()

                # 更新统计信息
                self.stats["flushes"] += 1
                self.stats["last_flush"] = time.time()

                logger.debug(f"缓存刷新成功: {self.get_stats()}")

            except Exception as e:
                logger.error(f"缓存刷新失败: {str(e)}")
                self.conn.rollback()

                # 记录失败次数和上次失败时间
                if not hasattr(self, "_flush_failures"):
                    self._flush_failures = 0
                    self._last_failure_time = 0

                self._flush_failures += 1
                self._last_failure_time = time.time()

                # 如果连续失败次数过多，考虑清空部分缓存以防内存泄露
                if self._flush_failures > 10:
                    logger.warning("缓存刷新连续失败10次以上")

                raise

    def _should_auto_flush(self) -> bool:
        """检查是否应该自动刷新缓存"""
        total_items = sum(len(items) for items in self._insert_cache.values())
        total_items += sum(len(items) for items in self._update_cache.values())
        total_items += sum(len(items) for items in self._delete_cache.values())

        return total_items >= self.max_batch_size

    def _auto_flush_worker(self) -> None:
        """后台自动刷新线程的工作函数"""
        while not self._stop_event.is_set():
            try:
                # 等待指定时间间隔或直到停止事件被设置
                if self._stop_event.wait(timeout=self.flush_interval):
                    break

                # 执行自动刷新
                with self._cache_lock:
                    if self._insert_cache or self._update_cache or self._delete_cache:
                        logger.debug("执行定时自动刷新")
                        self.flush()

            except Exception as e:
                logger.error(f"自动刷新线程发生错误: {str(e)}")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._cache_lock:
            # 计算当前缓存数量
            pending_inserts = sum(len(items) for items in self._insert_cache.values())
            pending_updates = sum(len(items) for items in self._update_cache.values())
            pending_deletes = sum(len(items) for items in self._delete_cache.values())

            stats = {
                **self.stats,
                "pending_inserts": pending_inserts,
                "pending_updates": pending_updates,
                "pending_deletes": pending_deletes,
                "total_pending": pending_inserts + pending_updates + pending_deletes,
            }

            return stats

    def shutdown(self) -> None:
        """关闭缓存系统，刷新所有缓存并停止后台线程"""
        logger.info("关闭数据库缓存系统...")
        self._stop_event.set()

        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5)

        # 最后刷新一次所有缓存
        try:
            self.flush()
        except Exception as e:
            logger.error(f"关闭时刷新缓存失败: {str(e)}")

        logger.info("数据库缓存系统已关闭")
