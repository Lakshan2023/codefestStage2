"""
Neo4j Connection Manager Module

This module provides a robust interface for connecting to and querying Neo4j databases.
It implements best practices for connection management, error handling, and resource cleanup.
"""

from typing import Any, List, Optional, Tuple, Union
from neo4j import GraphDatabase, Session, Transaction, Result, ResultSummary, Record
from neo4j.exceptions import ServiceUnavailable, AuthError
import logging
import os
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Neo4jConnectionError(Exception):
    """Custom exception for Neo4j connection-related errors."""
    pass

class Neo4jConnection:
    """
    A class to manage Neo4j database connections and queries.

    Attributes:
        uri (str): Neo4j database URI
        user (str): Database username
        _driver: Neo4j driver instance

    Example:
        >>> with Neo4jConnection.from_env() as conn:
        >>>     records = conn.query("MATCH (n) RETURN n LIMIT 5")
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        """
        Initialize Neo4j connection.

        Args:
            uri: Database URI
            user: Database username
            password: Database password

        Raises:
            Neo4jConnectionError: If connection cannot be established
        """
        try:
            self.uri = uri
            self.user = user
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            # Verify connection
            self._driver.verify_connectivity()
        except (ServiceUnavailable, AuthError) as e:
            raise Neo4jConnectionError(f"Failed to connect to Neo4j: {str(e)}") from e

        logger.info("Successfully connected to Neo4j database at %s", uri)

    @classmethod
    def from_env(cls) -> 'Neo4jConnection':
        """
        Create a connection using environment variables.

        Required environment variables:
            - NEO4J_URI
            - NEO4J_USER
            - NEO4J_PASSWORD

        Returns:
            Neo4jConnection instance

        Raises:
            Neo4jConnectionError: If required environment variables are missing
        """
        required_vars = ['NEO4J_URI', 'NEO4J_USERNAME', 'NEO4J_PASSWORD']
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            raise Neo4jConnectionError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        return cls(
            uri=os.getenv('NEO4J_URI'),
            user=os.getenv('NEO4J_USERNAME'),
            password=os.getenv('NEO4J_PASSWORD')
        )

    @contextmanager
    def get_session(self) -> Session:
        """
        Context manager for database sessions.

        Yields:
            Neo4j session object

        Raises:
            Neo4jConnectionError: If session creation fails
        """
        session = None
        try:
            session = self._driver.session()
            yield session
        except Exception as e:
            raise Neo4jConnectionError(f"Session error: {str(e)}") from e
        finally:
            if session:
                session.close()

    def query(
        self,
        query: str,
        parameters: Optional[dict] = None,
        include_summary: bool = False
    ) -> Union[List[Record], Tuple[List[Record], ResultSummary]]:
        """
        Execute a Cypher query and return the results.

        Args:
            query: Cypher query string
            parameters: Optional query parameters
            include_summary: Whether to include query summary in return value

        Returns:
            If include_summary is False: List of query records
            If include_summary is True: Tuple of (records list, query summary)

        Raises:
            Neo4jConnectionError: If query execution fails
        """
        try:
            with self.get_session() as session:
                logger.debug("Executing query: %s with parameters: %s", query, parameters)
                result = session.run(query, parameters)
                records = list(result)
                summary = result.consume() if include_summary else None

                logger.debug("Query completed successfully")
                return (records, summary) if include_summary else records

        except Exception as e:
            logger.error("Query failed: %s", str(e))
            raise Neo4jConnectionError(f"Query execution failed: {str(e)}") from e

    def close(self) -> None:
        """
        Close the database connection.
        """
        try:
            if self._driver:
                self._driver.close()
                logger.info("Database connection closed")
        except Exception as e:
            logger.error("Error closing connection: %s", str(e))
            raise Neo4jConnectionError(f"Failed to close connection: {str(e)}") from e

    def __enter__(self) -> 'Neo4jConnection':
        """Enable use as a context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Ensure connection is closed when exiting context."""
        self.close()

def main() -> None:
    """
    Example usage of the Neo4jConnection class.
    """
    try:
        with Neo4jConnection.from_env() as connection:
            # Example query
            query = "MATCH (n) RETURN n LIMIT 5"
            records, summary = connection.query(query, include_summary=True)

            # Process records
            for record in records:
                print(record)

            # Print query summary
            print(f"Query summary: {summary}")

    except Neo4jConnectionError as e:
        logger.error("Database operation failed: %s", str(e))
        raise
    except Exception as e:
        logger.error("Unexpected error: %s", str(e))
        raise

# if __name__ == "__main__":
#     main()
