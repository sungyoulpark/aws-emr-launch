# Copyright 2019 Amazon.com, Inc. and its affiliates. All Rights Reserved.
#
# Licensed under the Amazon Software License (the 'License').
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
#   http://aws.amazon.com/asl/
#
# or in the 'license' file accompanying this file. This file is distributed
# on an 'AS IS' BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

import boto3
import json
import logging
import traceback

from botocore.exceptions import ClientError

emr = boto3.client('emr')

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

PROFILES_SSM_PARAMETER_PREFIX = '/emr_launch/emr_profiles'
CONFIGURATIONS_SSM_PARAMETER_PREFIX = '/emr_launch/cluster_configurations'

ssm = boto3.client('ssm')


class EMRProfileNotFoundError(Exception):
    pass


class ClusterConfigurationNotFoundError(Exception):
    pass


def _get_parameter_value(ssm_parameter_prefix: str, name: str, namespace: str = 'default'):
    configuration_json = ssm.get_parameter(
        Name=f'{ssm_parameter_prefix}/{namespace}/{name}')['Parameter']['Value']
    return json.loads(configuration_json)


def _log_and_raise(e, event):
    trc = traceback.format_exc()
    s = 'Error processing event {}: {}\n\n{}'.format(str(event), str(e), trc)
    LOGGER.error(s)
    raise e


def handler(event, context):
    LOGGER.info('Lambda metadata: {} (type = {})'.format(json.dumps(event), type(event)))
    cluster_name = event.get('ClusterName', '')
    profile_namespace = event.get('ProfileNamespace', '')
    profile_name = event.get('ProfileName', '')
    configuration_namespace = event.get('ConfigurationNamespace', '')
    configuration_name = event.get('ConfigurationName', '')

    if not cluster_name:
        cluster_name = configuration_name

    emr_profile = None
    cluster_configuration = None

    try:
        emr_profile = _get_parameter_value(
            ssm_parameter_prefix=PROFILES_SSM_PARAMETER_PREFIX,
            namespace=profile_namespace,
            name=profile_name)
        LOGGER.info(f'ProfileFound: {json.dumps(emr_profile)}')
    except ClientError as e:
        if e.response['Error']['Code'] == 'ParameterNotFound':
            LOGGER.error(f'ProfileNotFound: {profile_namespace}/{profile_name}')
            raise EMRProfileNotFoundError(f'ProfileNotFound: {profile_namespace}/{profile_name}')
        else:
            _log_and_raise(e, event)
    try:
        cluster_configuration = _get_parameter_value(
            ssm_parameter_prefix=CONFIGURATIONS_SSM_PARAMETER_PREFIX,
            namespace=configuration_namespace,
            name=configuration_name)['ClusterConfiguration']
        LOGGER.info(f'ConfigurationFound: {json.dumps(cluster_configuration)}')
    except ClientError as e:
        if e.response['Error']['Code'] == 'ParameterNotFound':
            LOGGER.error(f'ConfigurationNotFound: {configuration_namespace}/{configuration_name}')
            raise ClusterConfigurationNotFoundError(f'ConfigurationNotFound: {configuration_namespace}/{configuration_name}')
        else:
            _log_and_raise(e, event)

    try:
        cluster_configuration['Name'] = cluster_name
        cluster_configuration['LogUri'] = \
            f's3://{emr_profile["LogsBucket"]}/elasticmapreduce/{cluster_name}'
        cluster_configuration['JobFlowRole'] = emr_profile['Roles']['InstanceRole'].split('/')[-1]
        cluster_configuration['ServiceRole'] = emr_profile['Roles']['ServiceRole'].split('/')[-1]
        cluster_configuration['AutoScalingRole'] = emr_profile['Roles']['AutoScalingRole'].split('/')[-1]
        cluster_configuration['Instances']['EmrManagedMasterSecurityGroup'] = \
            emr_profile['SecurityGroups']['MasterGroup']
        cluster_configuration['Instances']['EmrManagedSlaveSecurityGroup'] = \
            emr_profile['SecurityGroups']['WorkersGroup']
        cluster_configuration['Instances']['ServiceAccessSecurityGroup'] = \
            emr_profile['SecurityGroups']['ServiceGroup']
        cluster_configuration['SecurityConfiguration'] = emr_profile.get('SecurityConfigurationName', None)

        LOGGER.info(f'ClusterConfig: {json.dumps(cluster_configuration)}')
        return cluster_configuration

    except Exception as e:
        _log_and_raise(e, event)