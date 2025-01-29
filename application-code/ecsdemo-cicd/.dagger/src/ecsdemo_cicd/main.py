from typing import Annotated
import dagger
from dagger import dag, function, object_type, Doc, DefaultPath
import boto3


@object_type
class EcsdemoCicd:
    # these are used for each function in our module, so we added them as a constructor:
    # Learn more about constructors in the documentation https://docs.dagger.io/api/constructor/
    image: Annotated[str, Doc("The base image to use for building the container")] = "public.ecr.aws/docker/library/alpine:latest"
    registry: Annotated[str, Doc("The registry to push the image to")] = "ttl.sh/ecsdemo-cicd"

    @function
    def build(self, dir: Annotated[dagger.Directory, Doc("The directory containing the source code"), DefaultPath(".")]) -> dagger.Container:
        """Build the base image for the application."""
        return (
            dag.container()
            .from_(self.image)
            .with_workdir("/app")
            .with_mounted_directory("/app", dir)
            .with_exec([
                "apk",
                "add",
                "--no-cache",
                "python3",
                "py3-pip",
            ])
            .with_exec([
                "python3",
                "-m",
                "venv",
                "/path/to/venv",
            ])
            .with_exec(["/path/to/venv/bin/pip3", "install", "-r", "requirements.txt"])
        )

    @function
    async def push(self, dir: Annotated[dagger.Directory, Doc("The directory containing the source code"), DefaultPath(".")]) -> str:
        """Build and push the image for the application."""
        return await (
            self.build(dir)
            .with_entrypoint(["/path/to/venv/bin/python3", "app.py"])
            .publish(self.registry)
        )

    @function
    def run(self, dir: Annotated[dagger.Directory, Doc("The directory containing the source code"), DefaultPath(".")]) -> dagger.Service:
        """Build and run the application locally."""
        return (
            self.build(dir)
            .with_exposed_port(80)
            .as_service(args=["/path/to/venv/bin/python3", "app.py"])
        )

    @function
    async def deploy(
        self,
        cluster: Annotated[str, Doc("The name of the cluster for the application")],
        dir: Annotated[dagger.Directory, Doc("The directory containing the source code"), DefaultPath(".")],
        access_key: Annotated[dagger.Secret, Doc("The AWS access key for deploying the application")],
        secret_key: Annotated[dagger.Secret, Doc("The AWS secret key for deploying the application")],
        session_token: Annotated[dagger.Secret, Doc("The AWS session token for deploying the application")],
        region: Annotated[str, Doc("The region for the application")],
        service: Annotated[str, Doc("The ECS Service to update")],
        task_definition_family: Annotated[str, Doc("The Task Defintion name to update")],
        image: Annotated[str | None, Doc("The image to use for updating the service")],
    ) -> str:
        """Deploy the application to the ECS service"""
        # push the container image to the registry
        image = await self.push(dir)

        """Deploy the new image to the ECS Service"""
        ecs_client = boto3.client(
            'ecs',
            aws_access_key_id=await access_key.plaintext(),
            aws_secret_access_key=await secret_key.plaintext(),
            aws_session_token=await session_token.plaintext(),
            region_name=region
        )

        try:
            # Retrieve the most recent revision of the task definition family
            response = ecs_client.list_task_definitions(
                familyPrefix=task_definition_family,
                sort='DESC',
                maxResults=1
            )

            if not response['taskDefinitionArns']:
                raise ValueError(f"No task definitions found for family: {task_definition_family}")

            # Get the most recent task definition ARN
            latest_task_definition_arn = response['taskDefinitionArns'][0]

            print(f"Latest task definition ARN: {latest_task_definition_arn}")

            # describe the task definition
            task_definition = ecs_client.describe_task_definition(
                taskDefinition=latest_task_definition_arn
            )['taskDefinition']

            # Update the container image with the provided SHA
            for ctr in task_definition['containerDefinitions']:
                if 'image' in ctr:
                    ctr['image'] = image


            # Register the new task definition
            new_task_definition = ecs_client.register_task_definition(
                family=task_definition['family'],
                containerDefinitions=task_definition['containerDefinitions'],
                volumes=task_definition.get('volumes', []),
                taskRoleArn=task_definition.get('taskRoleArn'),
                executionRoleArn=task_definition.get('executionRoleArn'),
                networkMode=task_definition.get('networkMode'),
                requiresCompatibilities=task_definition.get('requiresCompatibilities', []),
                cpu=task_definition.get('cpu'),
                memory=task_definition.get('memory')
            )

            new_task_definition_arn = new_task_definition['taskDefinition']['taskDefinitionArn']

            print(f"New task definition registered: {new_task_definition_arn}")

            # update the service
            ecs_client.update_service(
                cluster=cluster,
                service=service,
                taskDefinition=new_task_definition_arn
            )

            return f"Service {service} updated to use task definition {new_task_definition_arn}"

        except Exception as e:
            print(f"Error updating ECS service: {e}")
            raise
