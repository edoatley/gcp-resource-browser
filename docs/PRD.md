# **Project Requirements Document: GCP Resource Explorer**

# **Executive Summary**

As organizations scale their cloud footprint, visibility becomes a primary challenge. This project aims to build the
**GCP Resource Explorer**, a unified toolset designed to provide instantaneous visibility across massive Google Cloud
environments—specifically targeting organizations managing 2000+ projects.

Current limitations in standard tooling make it difficult to quickly audit or search for resources at this scale. The
GCP Resource Explorer will solve this by offering:

* A high-performance **CLI** for developers and operators to query resources across projects with human-friendly
  formatting.
* A robust **HTTP API** built in Python to allow programmatic access and integration with existing internal dashboards.

# **Objectives**

The primary goal is to bridge the gap between complex cloud architectures and operational clarity.

## **1. Enhanced CLI Experience**

* **Custom Console Output:** Utilize a Python CLI framework (such as `Typer` combined with `Rich`) to generate highly
  customizable, colorized data tables that highlight key details over verbose defaults.
* **Global Search:** Enable cross-project searching for resources (e.g., finding a specific VM by name across 2000
  projects) without sequential API calls.
* **Rich Filtering:** Implement intuitive flags to filter by labels, regions, and resource types.

## **2. High-Performance HTTP API**

- **Python-Based Backend:** Leverage the Python ecosystem (FastAPI) for rapid development.
- **OpenAPI Specification:** Automatically generate and expose an `openapi.yml` (and Swagger UI) to ensure the API is
  well-documented and ready for broader team adoption.

* **Aggregated Responses:** Provide endpoints that aggregate data from multiple GCP sources into a single optimized
  payload.
* **Scalability:** Ensure the API can handle high-concurrency requests and return results for large project lists within
  acceptable latency thresholds.

# **Architecture & Trade-offs**

The technical direction of the tool is dictated by the need for speed and accuracy at scale.

## **Data Source: Standard GCP APIs vs. Cloud Asset Inventory (CAI)**

| Feature                | Standard GCP APIs                                | Cloud Asset Inventory (CAI)                 |
|:-----------------------|:-------------------------------------------------|:--------------------------------------------|
| **Speed**              | Slow (requires iterating through 2000+ projects) | Fast (centralized snapshot/search)          |
| **Rate Limits**        | High risk of hitting API quotas                  | Optimized for bulk exports and search       |
| **Real-time Accuracy** | High (live data)                                 | Near real-time (slight latency in indexing) |
| **Recommendation**     | Not selected for discovery                       | **Selected** for core search functionality  |

**Decision:** We will use **Cloud Asset Inventory (CAI)** as the primary data engine. Querying individual project APIs
sequentially for 2000 projects is non-viable due to latency and rate-limiting. CAI allows for "Search All Resources"
across an entire Organization or Folder.

## **Web Framework: Flask vs. FastAPI**

| Feature           | Flask                                  | FastAPI                              |
|:------------------|:---------------------------------------|:-------------------------------------|
| **Concurrency**   | Synchronous by default                 | Native `asyncio` support             |
| **Documentation** | Requires third-party plugins (Swagger) | Automatic OpenAPI/Swagger generation |
| **Performance**   | Standard                               | High (comparable to Go/NodeJS)       |
| **Decision**      | Not selected                           | **Selected**                         |

**Decision:** **FastAPI** is selected for the HTTP API. Its asynchronous nature is ideal for making non-blocking calls
to GCP services, and the automatic documentation generation significantly reduces maintenance overhead for internal
teams.

## **Resource Scope & Filtering**

The API and CLI will default to querying **all** resource types, but will implement a "noise reduction" filter to hide
highly verbose or low-value resources by default (e.g., standard IAM roles, default network routes, or system-managed
resources). **Decision:** Include an explicit `--show-all` (CLI) or `?show_all=true` (API) flag to bypass the filter and
display literal, unfiltered resource lists when deep auditing is required.

# **Open Questions**

# **Resolved Decisions**

- **Target Audience:** Initially developed for single-user/local execution, but architected with OpenAPI standards for
  eventual expansion to the wider engineering team.
- **Resource Scope:** The tool will query all resources by default but filter out common "noise" unless explicitly
  requested via a flag.
- **Authentication & Authorization:** The tool will rely on Application Default Credentials (ADC) via locally
  authenticated `gcloud` credentials (`gcloud auth application-default login`), allowing it to adapt to standard service
  accounts seamlessly in the future.
- **Hosting Environment:** The API will be executed locally during initial development, with a defined maturation path
  to be packaged and run within a Docker container for simplified distribution.