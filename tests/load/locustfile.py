from locust import HttpUser, task, between


class OpsPilotUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task
    def check_health(self):
        self.client.get("/")
